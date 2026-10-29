"""LLM 视觉起稿：姿势库动作描述（pose_actions 表）的批量自动起草。

方法与人工范式一致（完整记录见 docs/姿势库.md「动作描述怎么写」）：
- 看该动作该视角的缩略图 → LLM 写「身体事实 + 机位事实」的中文动作描述；
- few-shot 用人工定稿的 5 条样板（全身/半身/双人/前屈侧面/俯拍 各有代表）；
- 双人（2人絡み）强制甲/乙标签约定；不写身份/服装/画风 —— 线稿没有这些；
- 落库 source="ai_draft"（人工改稿 = charvar pose action --set 或网页编辑 UI）；
- 幂等：已写描述的动作视角自动跳过（--overwrite 重写）。
图片以 data URL 进 messages；当前配置的 LLM 不支持视觉时调用会失败 ——
用 --model 换一个视觉模型（OpenRouter 上如 gemini/gpt/qwen-vl 系）。
"""
from __future__ import annotations

import base64
import re

from charvar import poses as poses_service
from charvar import llm

# 校验：中文必有、长度合理；双人必须有甲/乙
_CJK_RE = re.compile(r"[一-鿿]")
_IDENT_HINTS = ("发色", "发型", "服装", "画风", "image1", "image2", "image3", "<image")

_ANGLE_HINT = {
    "正面": "正面机位（人物大体朝向镜头）",
    "上から": "俯拍机位（从上往下看，注意透视与前景遮挡）",
    "横": "正侧面机位（人物侧影）",
    "斜め": "斜 3/4 机位（介于正面与侧面之间）",
    "下から": "仰拍机位（从下往上看，注意透视与前景遮挡）",
}

# 样板 = 人工定稿的代表例（含用户原稿 P001）；键 (pose_id, angle)
FEWSHOT: list[tuple[str, str, str]] = [
    (
        "full_sexy_P001",
        "正面",
        "身体基本朝向镜头，腰胯轻微扭转，呈自然的 S 形曲线；画面右侧的手臂向上抬起，"
        "肘部向外弯曲，手放在头顶附近；画面左侧的手臂弯曲，手掌叉在腰侧，肘部向外展开。"
        "重心主要落在画面左侧的腿上，该腿较直；画面右侧的腿向侧方伸出，膝盖略微弯曲，"
        "脚尖轻点地面、脚跟抬起。整体姿态舒展、自信，带一点俏皮感。",
    ),
    (
        "full_sexy_P005",
        "正面",
        "单腿站立的抱膝姿势：画面右侧的腿伸直承重、脚平踩地面；画面左侧的腿向上高抬，"
        "膝盖拉到胸口高度，小腿向上折起、脚背抬到肩膀附近的高度；双臂弯曲从上方抱住"
        "这条抬起的膝与小腿，手指在胫前相扣；躯干保持挺立、背后微仰，头部端正向镜头；"
        "整体是平衡感很强的单腿抱膝展体站姿。",
    ),
    (
        "full_sexy_P014",
        "正面",
        "站立前屈（侧面）：双腿伸直挺立在画面左侧，上身从髋部对折前弯、胸腔压向大腿，"
        "臀部在画面左上高高挺起；双臂伸直向下、双手平按在身前的地面（画面右下）；"
        "头部垂低、脸朝地面方向；整体是臀部朝上、双手触地的深前屈。",
    ),
    (
        "upper80v2_P002",
        "正面",
        "身体正面朝向镜头，头部正直居中；画面两侧的手臂同时向上高举，肘部向外展开、"
        "略高于肩线，前臂向上收拢，双手举到头顶上方、指尖轻轻相对合成一个心形；"
        "肩线舒展，躯干挺直，没有侧倾或扭转。整体上半身姿态干净利落，带一点甜美俏皮的展示感。",
    ),
    (
        "pair60_P010",
        "正面",
        "甲（后方人物）坐在地面，两腿分开、膝盖弯曲立起，双手从身后环抱住乙的腰腹；"
        "乙（前方人物）侧坐在甲的膝上，背靠在甲的胸口，头微微后仰，双腿屈膝收拢、"
        "两脚并拢收到一侧；乙一只手搭在自己的膝头附近，另一只手轻扶甲的膝盖。"
        "两人身体紧密贴合，重心由甲承托，整体是安静、被环抱的构图。",
    ),
]


def thumb_data_url(pose_id: str, angle: str) -> str:
    """该动作该视角的缩略图 → jpeg data URL（300×400，单张几十 KB）。"""
    thumb = poses_service.thumb_image_file(pose_id, angle)
    b64 = base64.b64encode(thumb.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _instructions(title_zh: str, title: str, angle: str, line: str) -> str:
    shot = "\n".join(
        f"【{pid} · {ang}】\n{text}" for pid, ang, text in FEWSHOT
    )
    pair_rule = (
        "若图里是两个人物：全文用「甲（位置/角色说明）」「乙（位置/角色说明）」指称，"
        "绝不写 imageN；\n4a. 若是单人动作：直接以「她/人物」描述，不区分甲乙。\n"
        if line == "2人絡み"
        else "4. 全文只有一个人物，直接以「她/人物」描述，不出现甲乙。\n"
    )
    return (
        "你是姿势参考库的动作描述起草员。给你一张灰色无脸人偶线稿图"
        f"（动作「{title_zh}（{title}）」的{angle}视角，{_ANGLE_HINT.get(angle, angle)}），"
        "写出这段动作在出图提示词里可直接使用的中文动作描述。\n\n"
        "写作规则：\n"
        "1. 只写「身体在做什么 + 这个机位看到什么」：四肢/躯干/头怎么摆、重心与承重腿、"
        "朝向（面朝画面左方…）、机位事实（俯视角：机位在人物上前方…）、近端遮挡与透视大小。\n"
        "2. 不写角色身份：不发色发型、不写服装配饰、不写表情五官、不写画风 —— 人偶没有这些。\n"
        "3. 中文 80–160 字，一段成文，句子可直接拼在「摆出姿势：」之后。\n"
        + pair_rule +
        "5. 与下面的样板同一文风：一句一个事实，别堆形容词。\n\n样板：\n" + shot +
        f"\n\n现在给动作「{title_zh}（{title}）」的{angle}视角写描述。直接输出描述文本，不要任何解释。"
    )


def _validate(text: str, line: str) -> str:
    """校验起稿文本；返回清理后的文本，不合格抛 ValueError。"""
    text = str(text or "").strip().strip("“”\"' ")
    text = re.sub(r"^(摆出姿势|动作描述)[:：]\s*", "", text)
    if not _CJK_RE.search(text):
        raise ValueError("没有中文内容")
    if not 30 <= len(text) <= 400:
        raise ValueError(f"长度不合适：{len(text)} 字")
    if "2人絡み" == line and not ("甲" in text and "乙" in text):
        raise ValueError("双人动作必须用甲/乙指称两个人物")
    lowered = text.lower()
    if any(h in lowered for h in _IDENT_HINTS):
        raise ValueError("写了身份/服装/画风/imageN —— 描述里不该出现")
    return text


def _call_llm(messages: list[dict], model: str | None) -> str:
    """单次视觉调用（集中在此便于测试替换）；text 输出，不走 JSON。"""
    return llm.chat(messages, model=model, json_mode=False)


def describe_one(
    conn,
    pose_id: str,
    angle: str = "正面",
    *,
    model: str | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> dict:
    """给一个动作视角起稿；已写且不 overwrite → skipped。"""
    row = conn.execute(
        "SELECT line, number, title FROM pose_images WHERE pose_id = ? AND angle = ?",
        (pose_id, angle),
    ).fetchone()
    if row is None:
        return {"pose_id": pose_id, "angle": angle, "status": "error", "error": "库中没有该动作视角"}
    current = poses_service.pose_action(conn, pose_id, angle)["action_text"]
    if current.strip() and not overwrite:
        return {"pose_id": pose_id, "angle": angle, "status": "skipped", "text": current}
    from charvar.pose_translations import POSE_TITLE_ZH

    title_zh = POSE_TITLE_ZH.get(pose_id, "").strip() or row["title"]
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _instructions(title_zh, row["title"], angle, row["line"])},
                {"type": "image_url", "image_url": {"url": thumb_data_url(pose_id, angle)}},
            ],
        }
    ]
    draft = _call_llm(messages, model)
    try:
        draft = _validate(draft, row["line"])
    except ValueError as exc:
        return {"pose_id": pose_id, "angle": angle, "status": "error", "error": str(exc), "text": draft}
    if dry_run:
        return {"pose_id": pose_id, "angle": angle, "status": "draft", "text": draft}
    data = poses_service.set_pose_action(conn, pose_id, angle, draft, source="ai_draft")
    return {"pose_id": pose_id, "angle": angle, "status": "done", "text": data["action_text"]}


def describe_batch(
    conn,
    *,
    angle: str = "正面",
    limit: int = 0,
    model: str | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> dict:
    """按浏览次序给缺描述的动作视角批量起稿；返回汇总。"""
    data = poses_service.list_poses(conn)
    todo = [
        p["pose_id"]
        for p in data["poses"]
        if not any(
            im["angle"] == angle and (im["action_text"] or "").strip() for im in p["images"]
        )
    ]
    todo = todo[:limit] if limit and limit > 0 else todo
    results: list[dict] = []
    for pose_id in todo:
        result = describe_one(
            conn, pose_id, angle, model=model, dry_run=dry_run, overwrite=overwrite
        )
        results.append(result)
        head = (result.get("text") or result.get("error") or "")[:40]
        print(f"[{result['status']:>7}] {pose_id} · {angle} | {head}")
    summary = {
        "angle": angle,
        "done": sum(1 for r in results if r["status"] == "done"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "draft": sum(1 for r in results if r["status"] == "draft"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
    return summary
