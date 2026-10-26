# round-12 P5 复验（真重启后）

> 真重启：taskkill 28016 → ensure_up → **pid 3164**（127.0.0.1:9212，见
> review/.runtime/server-restarts.log 与 dhole-9212.log banner）。
> 复验探针：scripts/verify_r12.py，台账 sessions/verify-r12.log。

## V1 R11-001 维持：feed max_items 负数具名拒 / 0=all —— PASS

- `max_items=-3` → `invalid_request: max_items must be ≥ 0 (0 = all), got -3`（真机往返）
- `max_items=0` → 8 条全量（=all 语义保持）

## V2 pin 命中（本轮核心回归）—— PASS

- fill+read(property:value, as=who) 铸值 `verifyr12` → 第二发同 url/session/pin
  回读 `verifyr12`，summary 带 "pin hit" 句——页保持、寄存器一致、双账齐全。

## V3 第一拒形状 —— PASS

- GET → `invalid_request: method=GET is a read…`（码前缀+出路）
- css_selector #no-such → summary 内 `was NOT applied: it matched 0 elements…`

## 全量测试

- pytest：**7771 passed, 23 skipped**（123.66s，顺序跑）——与 round-11 收官计数持平
  （本轮 0 fix，无新增测试，数平）。
- ruff：16 errors——在册基线原样（未动代码）。

## 复验过程的一笔真话

首轮 verify 三发全 FAIL——真重启后**进程内 cred_defaults 清零**，127.0.0.1 被私网门
拦下（`URL targets internal/private IP`）。补 `allow_private` 后三发全过。这不是缺陷：
私网门在重启后重新武装正是它的本分，画像卡每张先置 cred_defaults 的设计此刻兑现。
