"""主体档案锁定项的摘除（换装 / 清除衣物用）。

为什么需要它：主体档案的 `outfit_lock` 锁着角色原本那套衣服。换装时它继续生效
就等于同时要求两套服装（旧的那套 + 服装预设这套），模型会跟着旧的那套穿——
「服装预设等于没生效」。所以「这条服装取代主体的默认服装」必须真的把旧锁摘掉。

摘锁的粒度与边界（换装不能把身份一起丢掉）：
- `outfit_lock` 里讲**衣物本体**的条目：clothing=replace 时整条摘掉。
- 鞋袜与配件（帽/眼镜/耳钉/首饰/围巾/包…）：accessories=remove 时按**子句**摘，
  不整条丢——一条 identity_lock 常同时写脸、发型与耳钉，整条丢会把脸和发型
  一起丢掉（换装要保留脸与发型发色）。
- 负向声明（"无随身配饰"/"耳朵上没有任何佩戴物"）必须保留：它本来就是「不戴」
  的意思，删掉等于让模型自己加配饰。
- `extra` 里「保持同一套默认服装」这类服装一致性短语：clothing=replace 时短语级
  摘掉，脸/发型/肤色等身份一致性留下。

本模块只做「临时主体副本」：原档案不动，不写回 DB。
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from charvar.models import SubjectProfile


class SubjectPolicy(BaseModel):
    """服装预设对主体档案锁定项的处理策略。

    clothing    replace = 这条服装取代默认服装 → 摘掉 outfit_lock 里的衣物条目
                keep    = 保留默认衣物（只清鞋袜与配件）
                add     = 叠加型（丝袜等单件穿搭）：保留默认衣物，只摘**袜类**子句
                          （原袜让位给这条；鞋帽包等其余配件保留）
    accessories remove  = 摘掉鞋袜与配件（outfit_lock + identity_lock 的配件子句）
                keep    = 保留配件（配件属于身份锚时用，如眼镜是角色的识别特征）
    """

    model_config = ConfigDict(extra="forbid")

    clothing: Literal["replace", "keep", "add"] = "keep"
    accessories: Literal["remove", "keep"] = "keep"


# 叠加型（clothing=add）要摘掉的「袜类」词：原衣物的袜类描述让位（换新袜），其余
# 衣物与鞋/帽/包/首饰等配件全保留 —— 比 accessories=remove 细得多（那个把鞋也摘）。
_STOCKING_WORDS = (
    "袜",                            # 丝袜/长袜/袜类/过膝袜/连裤袜/渔网袜 …全被这个字吃到
    "stocking", "stockings", "pantyhose", "tights", "fishnet", "socks", "hosiery",
)


# 主体锁定项分两类：讲「衣物本体」的，和讲「鞋袜/配件」的。
_CLOTHING_WORDS = (
    "上衣", "上装", "上身穿", "上身是", "下身", "下装", "下身穿",
    "外套", "夹克", "外袍", "长袍", "风衣", "大衣", "西装", "礼服",
    "背心", "衬衫", "毛衣", "高领衫", "针织衫", "针织裙", "连体衣",
    "泳衣", "比基尼", "内衣", "内搭", "卫衣", "裙", "裤", "服装", "衣物",
    "面料", "衣身", "衣摆", "衣片", "布料", "袖",
)
_ACCESSORY_WORDS = (
    "鞋", "靴", "凉鞋", "拖鞋", "袜", "鞋底", "鞋跟", "齿底",
    "眼镜", "墨镜", "耳钉", "耳环", "耳饰", "耳坠", "项链", "首饰", "珠宝",
    "帽", "头饰", "发饰", "发夹", "发箍", "发卡", "围巾", "丝巾", "披肩",
    "手套", "包袋", "背包", "手包", "配饰", "佩戴物", "手表", "胸针",
    "glasses", "sunglasses", "earring", "earrings", "necklace", "jewelry",
    "hat", "scarf", "gloves", "bag", "accessor", "footwear",
    "shoes", "boots", "socks", "stockings",
)
_NEGATION_CN = (
    "不含", "不戴", "不配", "不佩戴", "未穿戴", "未穿",
    "没有任何", "没有佩戴", "光滑裸露", "无随身配饰", "无任何穿戴",
)
_NEGATION_EN_RE = re.compile(r"\b(?:no|not|without|bare|none)\b", re.I)

# identity_lock 的条目是「身份特征」（脸、发型发色、体态），配件混在里面时
# 只摘配件**子句**，其余必须留着——换装不能把脸和发型一起丢掉。
_IDENTITY_WORDS = (
    "脸", "眼", "眉", "鼻", "唇", "嘴", "肤", "发", "发型", "刘海", "辫",
    "身材", "体型", "体形", "成年", "女性", "男性", "岁", "气质", "骨骼", "五官",
    "妆", "面颊", "腮红", "睫毛", "下巴", "额头", "颧骨", "下颌", "脸型",
    "头身比", "肌肉", "柔软", "丰满", "胸", "腰", "臀", "大腿", "小腿",
    "face", "hair", "eye", "skin", "body", "figure", "shape", "woman", "man",
    "girl", "boy", "complexion",
)

# extra 是「跨场景一致性」的一句话，配件/服装短语在里面常独立成块
# （"...the same teal glasses and gold hoop earrings, the same mature adult woman..."）。
_EXTRA_PHRASE_SPLIT_RE = re.compile(r"[^、，,]+[、，,]?")

# 子句分隔符：分号与句号（中英）+ 逗号。顿号「、」不算——「不含围巾、手套、包袋」
# 是一个整体，拆开就只剩半句。
_CLAUSE_RE = re.compile(r"[^；;。！!？?，,]+[；;。！!？?，,]?")

# extra 里「保持同一套默认服装」这类服装一致性短语。
_EXTRA_OUTFIT_RE = re.compile(
    r"(?:与|和|、|,|，)?\s*(?:同一套默认服装|同一套服装|同一套衣服|同样的服装|"
    r"同样的衣服|这套默认服装|默认服装|原来的服装|the same outfit|same outfit|"
    r"her outfit|the outfit)\b",
    re.I,
)


def mentions_clothing(text: str) -> bool:
    """这段文字讲的是衣物本体吗？"""
    low = (text or "").lower()
    return any(w in low for w in _CLOTHING_WORDS)


def mentions_accessory(text: str) -> bool:
    """这段文字提到鞋袜或配件吗？"""
    low = (text or "").lower()
    return any(w in low for w in _ACCESSORY_WORDS)


def is_negation(text: str) -> bool:
    """这是「不穿戴」的负向声明吗（"无随身配饰"/"耳朵上没有任何佩戴物"）？

    词表收窄过：单独的「无」「没有」太泛（"帽子上没有绒球"是描述，不是「不戴帽子」），
    只认明确的穿戴否定。
    """
    if any(w in (text or "") for w in _NEGATION_CN):
        return True
    return bool(_NEGATION_EN_RE.search(text or ""))


def mentions_identity(text: str) -> bool:
    """这段文字讲的是身份特征吗（脸、发型发色、体态）？"""
    return any(w in (text or "") for w in _IDENTITY_WORDS)


def _split_clauses(text: str) -> list[str]:
    """按标点切成子句（保留分隔符，便于原样拼回）。"""
    return [c for c in _CLAUSE_RE.findall(text or "") if c.strip()]


def strip_accessory_clauses(text: str) -> str:
    """摘掉子句里的鞋袜与配件描述（负向声明保留）。

    粒度是**子句**而不是整条：一条 identity_lock 常常同时写着脸、发型与耳钉，
    整条丢会把脸和发型一起丢掉（换装要保留脸与发型发色）。
    """
    if not text:
        return text
    kept = [
        c
        for c in _split_clauses(text)
        if not (mentions_accessory(c) and not mentions_clothing(c) and not is_negation(c))
    ]
    return "".join(kept).strip().strip("，,、；; ")


def strip_outfit_lock_entry(text: str) -> str:
    """outfit_lock 条目：纯配件整条删；衣物与配件混在一起的删配件子句。

    子句删完还剩「翻边厚实」「系带系好」这种碎片（依附于被删掉的主体）时，
    整条一并删掉——残句进提示词只会让模型困惑。
    """
    if not text.strip():
        return ""
    if not mentions_accessory(text):
        return text
    if not mentions_clothing(text):
        # 纯配件条目：负向声明（"无随身配饰"）保留，其余整条删
        return text if is_negation(text) else ""
    stripped = strip_accessory_clauses(text)
    return stripped if mentions_clothing(stripped) else ""


def mentions_stocking(text: str) -> bool:
    """这段文字讲的是袜类吗（叠加型要替换的那种；英文按词匹配不误吸 other-socks 词）。"""
    low = (text or "").lower()
    if any(w in low for w in ("袜",)):
        return True
    return bool(re.search(r"\b(?:stockings?|pantyhose|tights|fishnet|socks|hosiery)\b", low))


def strip_stocking_clauses(text: str) -> str:
    """按**子句**摘掉袜类描述（含「未穿戴袜类」这类负向声明 —— 叠加新袜时要让位）。

    粒度与 strip_accessory_clauses 同款：只摘袜类子句，同条里的衣物/鞋/帽/包都留下。
    叠加型的语义是「换了这双袜，别的不动」，负向声明（"未穿袜类"）与新袜并存也矛盾。
    """
    if not text:
        return text
    kept = [c for c in _split_clauses(text) if not mentions_stocking(c)]
    return "".join(kept).strip().strip("，,、；; ")


def strip_stocking_from_entry(text: str) -> str:
    """outfit_lock 条目级：先摘袜类子句；剩下的还是空壳（纯袜类条目的残句）就整条删。

    实测档案的粒度是**条目 = 一件衣物**：纯袜类条目（"双腿穿裸肤色薄透连脚
    丝袜，几乎完全透出…"）子句摘完只剩失主描述（"几乎完全透出…"），残句进
    提示词只会让模型困惑 —— 判法与 strip_outfit_lock_entry 同款：残句里还有
    其它衣物/配件内容才留。袜+鞋混一条的（制服少女）只摘袜、留鞋。
    """
    if not text.strip():
        return ""
    if not mentions_stocking(text):
        return text
    stripped = strip_stocking_clauses(text)
    if stripped and (mentions_clothing(stripped) or mentions_accessory(stripped)):
        return stripped
    return ""


# classic 拼装（composer）给叠加型服装段的前缀：没有这句，动作段+原衣物锁定项会
# 让模型当成两套服装打架。
CLOTHING_ADD_LEAD = "保留角色原有衣着不更换，仅在腿部叠加/换穿："


def merge_policies(policies: list[dict | SubjectPolicy | None]) -> dict | None:
    """把一批变体的策略合并成一个（**并集**：任一条要求摘除就摘除）。

    LLM 模式的档案块是整批共享的，没法逐条给不同版本；合并成并集的后果是
    「保留原衣物」的那条也拿不到旧衣物描述——那种条目应当单独跑一批。
    合并发生时调用方要给出声明（不静默）。
    """
    clean = [p for p in policies if p]
    if not clean:
        return None
    dumped = [
        p.model_dump() if isinstance(p, SubjectPolicy) else dict(p) for p in clean
    ]
    # 并集语义（任一条要求更激进的摘除就摘除）：替换 > 叠加 > 保留。
    rank = {"keep": 0, "add": 1, "replace": 2}
    clothing = max(dumped, key=lambda p: rank.get(p.get("clothing"), 0))["clothing"]
    accessories = (
        "remove"
        if any(p.get("accessories") == "remove" for p in dumped)
        else "keep"
    )
    return {"clothing": clothing, "accessories": accessories}


def outfit_policy_note(policy: dict | SubjectPolicy) -> str:
    """把策略翻成一句给 LLM 看的话（逐条意图携带）。

    LLM 模式整批共享一份档案，没法逐条换档案；所以把「本条不得出现什么」写进
    意图，让模型逐条照办。
    """
    if isinstance(policy, SubjectPolicy):
        policy = policy.model_dump()
    parts: list[str] = []
    if policy.get("clothing") == "replace":
        parts.append(
            "本条服装取代角色原本的衣着，不要写出角色原本那套衣服"
            "（帽子、外套、衬衫、长裤、鞋袜等一律不写）"
        )
    if policy.get("clothing") == "add":
        parts.append(
            "本条是叠加型穿搭（丝袜等单件）：保留角色原本的衣着不替换，"
            "仅把腿上的袜类换成本条袜类（原本没穿袜类就直接穿上）"
        )
    if policy.get("accessories") == "remove":
        parts.append(
            "不要写出角色的配件（眼镜、耳饰、帽子、围巾、手套、包袋、鞋袜等）"
        )
    if not parts:
        return ""
    return "；".join(parts) + "。"


def strip_extra_phrases(extra: str, *, drop_outfit: bool) -> str:
    """extra（跨场景一致性声明）里的服装 / 配件短语摘掉。

    两级：服装短语按**短语**删（"与同一套默认服装"），配件短语按**块**删
    （英文 extra 里配件常独立成块："the same teal glasses and gold hoop earrings,"）。
    """
    if not extra:
        return extra
    out = extra
    if drop_outfit:
        out = _EXTRA_OUTFIT_RE.sub("", out)
    kept = [
        c
        for c in _EXTRA_PHRASE_SPLIT_RE.findall(out)
        if not (mentions_accessory(c) and not mentions_identity(c))
    ]
    return "".join(kept).strip().strip("，,、；; ")


def apply_subject_policy(
    subject: SubjectProfile, policy: dict | SubjectPolicy | None
) -> SubjectProfile:
    """按服装预设声明的策略生成**临时**主体副本（档案本身不动，不写回 DB）。

    - clothing=replace：主体 outfit_lock 的衣物条目整条摘掉（这条服装取代默认服装）
    - clothing=add：保留衣物本体，只摘 outfit_lock 里的**袜类**子句（原袜让位）
    - accessories=remove：摘掉鞋袜与配件（outfit_lock 与 identity_lock 的配件子句）
    - clothing=replace：extra 里「保持同一套默认服装」这类服装一致性短语也摘掉，
      脸/发型/肤色等身份一致性保留
    """
    if policy is None:
        return subject
    if isinstance(policy, SubjectPolicy):
        policy = policy.model_dump()
    clothing = policy.get("clothing", "keep")
    accessories = policy.get("accessories", "keep")

    outfit_lock = list(subject.outfit_lock)
    identity_lock = list(subject.identity_lock)
    extra = subject.extra

    if clothing == "add":
        outfit_lock = [strip_stocking_from_entry(x) for x in outfit_lock]
        outfit_lock = [x for x in outfit_lock if x.strip()]
        extra = strip_stocking_clauses(extra)

    if accessories == "remove":
        outfit_lock = [strip_outfit_lock_entry(x) for x in outfit_lock]
        outfit_lock = [x for x in outfit_lock if x.strip()]
        identity_lock = [strip_accessory_clauses(x) for x in identity_lock]
        identity_lock = [x for x in identity_lock if x.strip()]
        # identity_lock 里整条只剩配件的（"眼镜是青绿色细框…；耳垂两侧各戴…"）直接丢弃
        identity_lock = [
            x for x in identity_lock
            if mentions_identity(x) or not mentions_accessory(x)
        ]
        extra = strip_extra_phrases(extra, drop_outfit=False)

    if clothing == "replace":
        # 衣物描述整条摘掉；只剩配件信息的条目保留（配件由 accessories 策略决定）
        outfit_lock = [x for x in outfit_lock if not mentions_clothing(x)]
        extra = strip_extra_phrases(extra, drop_outfit=True)

    if (
        outfit_lock == subject.outfit_lock
        and identity_lock == subject.identity_lock
        and extra == subject.extra
    ):
        return subject
    return subject.model_copy(
        update={
            "outfit_lock": outfit_lock,
            "identity_lock": identity_lock,
            "extra": extra,
        }
    )
