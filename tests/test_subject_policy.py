"""主体锁的摘除（换装 / 清除衣物用）：charvar/subject_policy.py。

背景：主体档案的 outfit_lock 锁着角色原本那套衣服。换装时它继续生效就等于
同时要求两套服装（旧的那套 + 服装预设这套），模型会跟着旧的那套穿。

本文件锁住四件事：
1. 衣物条目按 clothing=replace 整条摘掉，配件条目按 accessories=remove 摘掉；
2. identity_lock 只摘配件**子句**——脸、发型发色、体态必须留着；
3. 负向声明（"无随身配饰"）保留：删掉它等于让模型自己加配饰；
4. 摘的是**临时副本**，原档案不动。
"""
from __future__ import annotations

from charvar.models import SubjectProfile
from charvar.subject_policy import (
    apply_subject_policy,
    merge_policies,
    outfit_policy_note,
    strip_accessory_clauses,
    strip_extra_phrases,
    strip_outfit_lock_entry,
)

REPLACE = {"clothing": "replace", "accessories": "remove"}
KEEP = {"clothing": "keep", "accessories": "remove"}


def _subject(**kw) -> SubjectProfile:
    base = dict(
        id="demo",
        name="演示",
        style_lock=["自然真实摄影"],
        identity_lock=[
            "脸型是偏窄的鹅蛋脸，下颌线清晰",
            "深棕色头发剪成利落的短发，额前是齐整的厚重刘海；左耳垂上戴一枚小巧的黑色耳钉，右耳的耳垂光滑裸露",
        ],
        outfit_lock=[
            "头上戴一顶砖红色的针织翻边帽，帽身是细密的罗纹针织，翻边厚实，帽口贴合发顶",
            "上身穿一件焦糖棕色的羊毛短款夹克，前襟完全敞开不系扣",
            "脚上是一双深棕色的皮质马丁靴，靴筒到小腿下段，系带系好",
            "长裤背面有后腰育克线与两个简洁无装饰后贴袋；马丁靴有厚实鞋跟与齿底，帽后为针织罗纹",
            "无随身配饰。默认造型不含围巾、手套、包袋等。",
        ],
        extra="保持同一张脸、同一发型、同一肤色与同一套默认服装，在任意姿态与场景中都保持一致。",
    )
    base.update(kw)
    return SubjectProfile(**base)


# ---------- 分类与子句粒度 ----------

def test_outfit_lock_entry_classification():
    assert strip_outfit_lock_entry("上身穿一件焦糖棕色的羊毛短款夹克，前襟完全敞开不系扣") == (
        "上身穿一件焦糖棕色的羊毛短款夹克，前襟完全敞开不系扣"
    )
    # 纯配件条目整条删（不留"翻边厚实"这种依附于帽子的碎片）
    assert strip_outfit_lock_entry(
        "头上戴一顶砖红色的针织翻边帽，帽身是细密的罗纹针织，翻边厚实，帽口贴合发顶"
    ) == ""
    assert strip_outfit_lock_entry("脚上是一双深棕色的皮质马丁靴，靴筒到小腿下段，系带系好") == ""
    # 负向声明保留
    assert strip_outfit_lock_entry("无随身配饰。默认造型不含围巾、手套、包袋等。") != ""
    # 混合条目：删配件子句，留衣物
    mixed = strip_outfit_lock_entry(
        "长裤背面有后腰育克线与两个简洁无装饰后贴袋；马丁靴有厚实鞋跟与齿底，帽后为针织罗纹"
    )
    assert "长裤背面" in mixed
    assert "马丁靴" not in mixed
    assert "帽后" not in mixed


def test_identity_lock_strips_accessory_clause_only():
    out = strip_accessory_clauses(
        "深棕色头发剪成利落的短发，额前是齐整的厚重刘海；左耳垂上戴一枚小巧的黑色耳钉，右耳的耳垂光滑裸露"
    )
    assert "头发" in out
    assert "刘海" in out
    assert "耳钉" not in out


def test_extra_phrase_and_block_cleanup():
    # 服装短语按短语删
    out = strip_extra_phrases(
        "保持同一张脸、同一发型、同一肤色与同一套默认服装，在任意姿态与场景中都保持一致。",
        drop_outfit=True,
    )
    assert "默认服装" not in out
    assert "同一张脸" in out and "同一肤色" in out
    # 配件块按块删（英文 extra 里配件独立成块）
    out2 = strip_extra_phrases(
        "Keep the same face, the same blue almond eyes, the same teal thin-rimmed long oval glasses "
        "and gold hoop earrings, the same mature adult woman in her mid-thirties.",
        drop_outfit=True,
    )
    assert "glasses" not in out2
    assert "earrings" not in out2
    assert "same face" in out2 and "almond eyes" in out2


# ---------- apply_subject_policy ----------

def test_replace_clothing_removes_old_outfit():
    sub = _subject()
    out = apply_subject_policy(sub, REPLACE)
    joined = " ".join(out.outfit_lock)
    assert "夹克" not in joined
    assert "马丁靴" not in joined
    assert "针织翻边帽" not in joined
    # 脸与发型留着
    ident = " ".join(out.identity_lock)
    assert "鹅蛋脸" in ident
    assert "短发" in ident
    assert "耳钉" not in ident
    # extra 的服装一致性短语摘掉，身份一致性留着
    assert "默认服装" not in out.extra
    assert "同一张脸" in out.extra


def test_keep_clothing_keeps_outfit_but_drops_accessories():
    sub = _subject()
    out = apply_subject_policy(sub, KEEP)
    joined = " ".join(out.outfit_lock)
    assert "夹克" in joined
    assert "马丁靴" not in joined
    assert "针织翻边帽" not in joined
    # extra 的「同一套默认服装」保留（衣物还穿着）
    assert "默认服装" in out.extra


def test_subject_policy_does_not_touch_original():
    sub = _subject()
    before = list(sub.outfit_lock)
    out = apply_subject_policy(sub, REPLACE)
    assert sub.outfit_lock == before, "原档案不能被改"
    assert out is not sub


def test_no_policy_returns_same_object():
    sub = _subject()
    assert apply_subject_policy(sub, None) is sub


def test_merge_policies_is_union():
    assert merge_policies([None, None]) is None
    assert merge_policies([REPLACE, KEEP]) == {"clothing": "replace", "accessories": "remove"}
    assert merge_policies([KEEP, KEEP]) == {"clothing": "keep", "accessories": "remove"}
    assert merge_policies([{"clothing": "keep", "accessories": "keep"}]) == {
        "clothing": "keep",
        "accessories": "keep",
    }


def test_policy_note_is_readable():
    note = outfit_policy_note(REPLACE)
    assert "取代角色原本的衣着" in note
    assert "不要写出角色的配件" in note
    assert outfit_policy_note({"clothing": "keep", "accessories": "keep"}) == ""
