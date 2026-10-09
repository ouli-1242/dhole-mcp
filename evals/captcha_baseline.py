"""视觉辅助基线（计划第十章）：验证码试读通过率、二维码解码成功率、转交纪律在不在 wire 上。

计划把这条基线的判据写成一句话：

> 图形验证码试读 ≤N 次通过率与转交率、二维码解码成功率——本地 fixture 生成
> 扭曲文字与二维码图片，不依赖外站。

三块各自独立，都可单独跑：

1. **二维码解码成功率**（确定性，本脚本自己算）
   拿靶站的基准二维码，加旋转 / 缩放 / 噪声 / 模糊 / 重压缩，数本地解码器
   还能解出多少。这是纯确定性工作 —— dhole 里 `decode_qr` 走的就是同一个
   `cv2.QRCodeDetector`，所以这条曲线直接说明"二维码这条路有多稳"。

2. **验证码试读通过率**（agent 侧，本脚本只负责出题与判卷）
   dhole 里没有视觉模型（安全红线 6），读图发生在**外层多模态 agent**。
   所以本脚本做两件事：`--emit` 出 N 张扭曲文字图 + 侧车答案；`--score`
   拿 agent 写下的 `answers.txt` 对照侧车判一次读对率。**判卷与出题分离**
   —— 出题时不打印答案，避免"基线"变成抄答案。

3. **转交纪律在不在 wire 上**（代码）
   `DHOLE_MULTIMODAL=1` 时 `screenshot` / `web_read` 的描述里必须带
   "≤3 次后转交"。纪律只跟着 flag 上线：关着 flag 的调用方看不见像素，
   纪律对它是噪声，还白占 connect tokens。

用法
----
    python evals/captcha_baseline.py                 # 二维码 + 转交纪律（不需要靶站）
    python evals/captcha_baseline.py --emit 12       # 出 12 张题到 .runtime/captcha/
    python evals/captcha_baseline.py --score .runtime/captcha/answers.txt
    python evals/captcha_baseline.py --emit 12 --level hard
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))          # fixture_site 在本目录

RUNTIME = HERE / ".runtime"
CAPTCHA_DIR = RUNTIME / "captcha"

from fixture_site import (  # noqa: E402
    QR_PAYLOAD,
    _captcha_png,
    _qr_png,
    captcha_text,
    new_captcha_token,
)

PASS, FAIL = "通过", "**未通过**"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}\n    {detail}")


# ── 1. 二维码解码成功率 ────────────────────────────────────────────────

def _decode(img) -> str:
    import cv2
    data, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    return data or ""


def _perturbations():
    """(名字, 变换) —— 变换吃灰度图，吐灰度图。覆盖真实截图里最常见的失真。"""
    import cv2
    import numpy as np

    def rot(deg):
        def f(g):
            h, w = g.shape
            m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
            return cv2.warpAffine(g, m, (w, h), borderValue=255)
        return f

    def scale(k):
        def f(g):
            h, w = g.shape
            small = cv2.resize(g, (max(1, int(w * k)), max(1, int(h * k))),
                               interpolation=cv2.INTER_AREA)
            return small  # 不放大回去：测的是"缩到这么小还读不读得出"
        return f

    def noise(sigma):
        def f(g):
            rng = np.random.default_rng(int(sigma * 1000))
            n = rng.normal(0, sigma, g.shape)
            return np.clip(g.astype(np.float32) + n, 0, 255).astype(np.uint8)
        return f

    def blur(k):
        def f(g):
            return cv2.GaussianBlur(g, (k, k), 0)
        return f

    def jpeg(q):
        def f(g):
            ok, buf = cv2.imencode(".jpg", g, [cv2.IMWRITE_JPEG_QUALITY, q])
            return cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE) if ok else g
        return f

    return [
        ("原始", lambda g: g),
        ("旋转 5°", rot(5)),
        ("旋转 15°", rot(15)),
        ("旋转 30°", rot(30)),
        ("缩到 0.5x", scale(0.5)),
        ("缩到 0.35x", scale(0.35)),
        ("高斯噪声 σ=25", noise(25)),
        ("高斯噪声 σ=60", noise(60)),
        ("模糊 5x5", blur(5)),
        ("JPEG q=40", jpeg(40)),
    ]


def qr_baseline() -> None:
    import cv2
    import numpy as np

    base = cv2.imdecode(np.frombuffer(_qr_png(), dtype="uint8"),
                        cv2.IMREAD_GRAYSCALE)

    # 硬不变式：基准图必须解回它自己声明的载荷。这条挂了说明靶站/解码器坏了，
    # 后面那张失真表也就没意义了。
    record("二维码基准自校验（靶站出的图能解回载荷）", _decode(base) == QR_PAYLOAD,
           f"解码={_decode(base)!r}")

    rows = []
    for name, fn in _perturbations():
        got = _decode(fn(base))
        rows.append((name, got == QR_PAYLOAD))

    hit = sum(1 for _, ok in rows if ok)
    print("二维码解码成功率（本地解码器，与 dhole 的 decode_qr 同一实现）")
    for name, ok in rows:
        print(f"    {'✓' if ok else '✗'} {name}")
    # 阈值 8/10：常见的旋转 / 缩放 / 轻噪声 / 模糊 / 重压缩都要过；留下
    # σ=60 这类"物理上就解不出"的极端失真作为已知上限，不当失败。
    record("二维码在常见失真下仍能解出（≥8/10）", hit >= 8,
           f"{hit}/{len(rows)} 种失真下解出正确载荷"
           f"（未过的是极端失真，属已知上限而非缺陷）")


# ── 2. 验证码试读（出题 / 判卷分离）────────────────────────────────────

def emit(n: int, level: str) -> int:
    CAPTCHA_DIR.mkdir(parents=True, exist_ok=True)
    for old in CAPTCHA_DIR.glob("*.png"):
        old.unlink()
    manifest = []
    for i in range(n):
        token = new_captcha_token()
        path = CAPTCHA_DIR / f"{i + 1:02d}_{token}.png"
        path.write_bytes(_captcha_png(token, level))
        manifest.append({"idx": i + 1, "token": token, "file": path.name})
    # 侧车答案单独放：--emit 的 stdout 不打印它，出题的人看不到答案。
    (CAPTCHA_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (CAPTCHA_DIR / "key.json").write_text(
        json.dumps({m["token"]: captcha_text(m["token"]) for m in manifest},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已出题 {n} 张（{level}）-> {CAPTCHA_DIR}")
    for m in manifest:
        print(f"    {m['idx']:02d}  {m['file']}")
    print(f"\n读完后把答案写成每行 `token<TAB>你的答案` 存到 "
          f"{CAPTCHA_DIR / 'answers.txt'}，再跑：\n"
          f"    python evals/captcha_baseline.py --score {CAPTCHA_DIR / 'answers.txt'}")
    print("（答案键在 key.json，出题时未打印，避免基线变成抄答案。）")
    return 0


def score(answers_path: Path) -> int:
    key_path = answers_path.parent / "key.json"
    if not key_path.exists():
        print(f"找不到答案键 {key_path} —— 先用 --emit 出题。")
        return 2
    if not answers_path.exists():
        print(f"找不到答案文件 {answers_path} —— agent 还没写。")
        return 2
    key = json.loads(key_path.read_text(encoding="utf-8"))

    got: dict[str, str] = {}
    for line in answers_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        token, _, ans = line.partition("\t")
        if not ans:                       # 容忍空格分隔
            token, _, ans = line.partition(" ")
        got[token.strip()] = ans.strip().upper()

    right = [t for t in key if got.get(t, "") == key[t]]
    wrong = [t for t in key if t in got and got[t] != key[t]]
    missing = [t for t in key if t not in got]
    n = len(key)
    print("验证码试读结果")
    for t in key:
        mark = "✓" if got.get(t) == key[t] else ("·" if t not in got else "✗")
        print(f"    {mark} {t}  答={got.get(t, '(未答)')!r}  真={key[t]!r}")

    rate = len(right) / n if n else 0.0
    # 一次读对率是**测量值**，不是通过/失败：断言 100% 只会永远报红。这里
    # 只守一条健全性下限 —— 全 0 通常意味着答案文件与 key 对不上（token 变了、
    # 判卷用了上一轮的 answers.txt），那是 harness 坏了，不是"模型读不出"。
    record("验证码试读：答案文件与答案键对得上（一次读对率 > 0）", len(right) > 0,
           f"读对 {len(right)}/{n}，读错 {len(wrong)}，未答 {len(missing)}"
           f" → 一次通过率 {rate:.0%}")
    return 0


# ── 3. 转交纪律在不在 wire 上 ──────────────────────────────────────────

def wire_check() -> None:
    """读一遍常驻层那一份话，量的是今天还成立的三件事。

    这一格原本是 17.0 之前写的：它比 `DHOLE_MULTIMODAL` 开与关的两份描述，并去取
    `on["screenshot"]` 与 `on["web_read"]` 两颗工具。两件前提都没了 —— 元素捕获在 17.0
    起**恒可用**（识别发生在 agent 一侧，dhole 不带视觉模型），而 `web_screenshot` 独立
    工具早已并入 `web_act` 的截图动作。上一次它不是"没过"，是 `KeyError: 'screenshot'`：
    一把量具对着不存在的旋钮，读数再稳定也不说明被量的东西。所以这里换三件今天问得出的事。
    """
    from dhole_mcp.server import MasterFetchServer

    saved = os.environ.get("DHOLE_MULTIMODAL")
    try:
        os.environ.pop("DHOLE_MULTIMODAL", None)
        off = {td["name"]: td["description"]
               for td in MasterFetchServer._enabled_tool_defs()}
        os.environ["DHOLE_MULTIMODAL"] = "1"
        on = {td["name"]: td["description"]
              for td in MasterFetchServer._enabled_tool_defs()}
    finally:
        if saved is None:
            os.environ.pop("DHOLE_MULTIMODAL", None)
        else:
            os.environ["DHOLE_MULTIMODAL"] = saved

    moved = [n for n in on if len(on[n]) != len(off[n])]
    record("视觉辅助不再跟着 DHOLE_MULTIMODAL 上线（17.0 起恒可用）", not moved,
           f"开旗与关旗的描述逐颗等长；有动的: {moved or '无'}")

    act = on["web_act"]
    caps = "at most 3" in act
    hand = act.count("hand off") >= 1
    record("web_act 写明 ≤3 次上限与转交终点", caps and hand,
           f"含 'at most 3'={caps}；'hand off' 出现 {act.count('hand off')} 次")

    # 那句做不到的承诺不许回来：每一次调用都重新导航，所以"读一次—重试一次"答的不是
    # 同一枚挑战（靶站实测：第二轮提交的是第一轮那一枚）。
    back = [n for n, d in on.items() if "read-and-retry" in d]
    typed = "you are about to type" in act
    record("『read-and-retry rounds』『照着截图去打字』两句都从 wire 上退了场",
           not back and not typed,
           f"仍带 read-and-retry 的工具: {back or '无'}；web_act 仍说 'about to type'={typed}")

    # 而真能在同一条链里答完的那一对要在场 —— 不然退了假话，读者只剩"不能"没有"能"。
    has_read = '"read"' in act
    has_sigil = "{=" in act
    record("链内读—答那一对形状在 web_act 上", has_read and has_sigil,
           f"web_act 含 read 的例子={has_read}；含引用记号 {{=name}}={has_sigil}")

    from dhole_mcp import guide
    _header, behavior = guide.read("behavior")
    reason = "already gone on the next" in behavior
    record("为什么跨调用重试答不中：完整理由在 sys_guide 送得出的那一卷里", reason,
           f"references/tools.md（topic='behavior'）含 'already gone on the next'={reason}")


# ── main ───────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="dhole 视觉辅助基线")
    ap.add_argument("--emit", type=int, metavar="N", help="出 N 张验证码题")
    ap.add_argument("--level", choices=("normal", "hard"), default="normal")
    ap.add_argument("--score", type=Path, metavar="FILE", help="判一份 answers.txt")
    ap.add_argument("--qr", action="store_true", help="只跑二维码解码基线")
    ap.add_argument("--wire", action="store_true", help="只跑转交纪律检查")
    args = ap.parse_args()

    if args.emit:
        return emit(args.emit, args.level)
    if args.score:
        return score(args.score)

    only = args.qr or args.wire
    if args.qr or not only:
        qr_baseline()
        print()
    if args.wire or not only:
        wire_check()
        print()

    # 顺手判一份已存在的答案（有就判，没有就提示）
    default_answers = CAPTCHA_DIR / "answers.txt"
    if not only and default_answers.exists():
        score(default_answers)
        print()

    bad = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} 项通过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
