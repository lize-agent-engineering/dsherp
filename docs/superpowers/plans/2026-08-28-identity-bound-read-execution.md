# 阶段 3 参考：身份绑定与只读授权执行

当前拓扑与执行顺序以 [已确认的平台身份计划](2026-08-28-platform-identity.md) 为准。

> 使用 executing-plans，在当前任务顺序执行；本文件原有优先顺序已被 [前端优先计划](2026-08-28-frontend-agent-app-generation.md) 替代，保留有效授权测试设计；实施时结合真实 Frappe 认证、持久成员关系和第二隔离企业，不将本文件单独视为阶段 3 完成。

**目标：** 在已验证的 ERP 只读接口上增加服务端企业成员关系和任务归属约束。先让错误企业、错误用户、过期权限在请求发出前被拒绝，不扩展写入工具。

**架构：** 应用侧保存企业与 ERP Site 的映射，调用方必须来自已验证身份；ERP 仍复查当前用户权限。现有 `create_server()` 的身份比对继续保留。此参考分片不单独选择登录 UI/身份供应商，不提供可公开访问的匿名 HTTP 入口；认证接入和容器级租户执行隔离需要后续单独验收。

**技术栈：** Python 3.12.11、现有锁文件、pytest、真实 Frappe 15.118.0 只读 API。

**设计：** [首版设计](../specs/2026-08-28-dsherp-design.md)；实际接口见 [ERP 证据](../../engineering/erpnext-integration-evidence.md)。

## 约束

- 不接受模型生成的用户、Site、URL、凭证或企业成员关系。
- 无授权、成员撤销、任务归属不符都抛出 `PermissionError`，不切换管理员或其他站点。
- 不创建提交、取消、库存/账务工具；确认记录、幂等写入及站点开通服务仍属于后续分片，不用空实现占位。
- 不将下面的纯授权函数当作认证系统；只有服务器验证过的 `user_id` 才能调用它。公网服务发布前必须另验真实身份入口。

## 任务 1：企业绑定解析

新增 `dsherp/access.py`、`tests/test_access.py`。

接口：

```python
from dataclasses import dataclass
from collections.abc import Mapping, Set

@dataclass(frozen=True)
class SiteBinding:
    enterprise_id: str
    user_id: str
    site: str
    credential_ref: str  # 绑定到此企业的此用户，禁止企业内共用；不保存 key/secret

def resolve_binding(
    user_id: str,
    enterprise_id: str,
    memberships: Set[tuple[str, str]],
    bindings: Mapping[tuple[str, str], SiteBinding],
) -> SiteBinding:
    if not user_id or not enterprise_id or (user_id, enterprise_id) not in memberships:
        raise PermissionError("Enterprise membership required")
    binding = bindings.get((enterprise_id, user_id))
    if binding is None or binding.enterprise_id != enterprise_id or binding.user_id != user_id:
        raise PermissionError("Enterprise site unavailable")
    return binding
```

- [ ] 先写以下行为测试并执行 `.venv/bin/python -m pytest tests/test_access.py -q`，确认缺少实现失败：

```python
import pytest
from dsherp.access import SiteBinding, resolve_binding

def test_wrong_enterprise_never_resolves_a_binding():
    a = SiteBinding("a", "alice", "a.localhost", "a/alice")
    b = SiteBinding("b", "bob", "b.localhost", "b/bob")
    with pytest.raises(PermissionError):
        resolve_binding("alice", "b", {("alice", "a")}, {("a", "alice"): a, ("b", "bob"): b})

def test_member_gets_only_the_bound_site():
    a = SiteBinding("a", "alice", "a.localhost", "a/alice")
    assert resolve_binding("alice", "a", {("alice", "a")}, {("a", "alice"): a}) == a

def test_revoked_membership_fails_on_next_call():
    a = SiteBinding("a", "alice", "a.localhost", "a/alice")
    memberships = {("alice", "a")}
    resolve_binding("alice", "a", memberships, {("a", "alice"): a})
    memberships.clear()
    with pytest.raises(PermissionError):
        resolve_binding("alice", "a", memberships, {("a", "alice"): a})
```

- [ ] 最小实现：拒绝空 user/enterprise、不在 memberships 的组合、不存在或 enterprise_id/user_id 不匹配的 binding；全部合法才返回绑定对象。不缓存授权结果。

```python
def resolve_binding(user_id, enterprise_id, memberships, bindings):
    if not user_id or not enterprise_id or (user_id, enterprise_id) not in memberships:
        raise PermissionError("Enterprise membership required")
    binding = bindings.get((enterprise_id, user_id))
    if binding is None or binding.enterprise_id != enterprise_id or binding.user_id != user_id:
        raise PermissionError("Enterprise site unavailable")
    return binding
```

- [ ] 加入缺失绑定及同企业错用其他用户凭证的用例，运行同一测试命令直到通过。按功能提交。

```python
def test_missing_binding_does_not_choose_another_site():
    with pytest.raises(PermissionError):
        resolve_binding("alice", "a", {("alice", "a")}, {})

def test_same_enterprise_does_not_share_another_users_credentials():
    bobs_binding = SiteBinding("a", "bob", "a.localhost", "a/bob")
    with pytest.raises(PermissionError):
        resolve_binding("alice", "a", {("alice", "a")}, {("a", "alice"): bobs_binding})
```

## 任务 2：任务归属和每次执行复查

新增 `dsherp/read_execution.py`、`tests/test_read_execution.py`。

接口：`execute_read(user_id, enterprise_id, task_owner, memberships, bindings, read)`，其中 `task_owner` 是服务端存储的 `(enterprise_id, user_id)`；`read(binding)` 是仅服务端可传入的已存在只读调用，不向模型暴露任意回调或方法选择。

- [ ] 先写失败测试：错误任务归属不得调用下游；正确执行复用 `resolve_binding`，成员撤销后再次调用必须失败。

```python
import pytest
from dsherp.read_execution import execute_read
from dsherp.access import SiteBinding

def test_foreign_task_stops_before_erp_io():
    def forbidden_io(_binding):
        pytest.fail("Must reject before ERP I/O")
    a = SiteBinding("a", "alice", "a.localhost", "a/alice")
    with pytest.raises(PermissionError):
        execute_read("alice", "a", ("a", "bob"), {("alice", "a")}, {("a", "alice"): a}, forbidden_io)

def test_authorized_read_returns_real_callable_result():
    a = SiteBinding("a", "alice", "a.localhost", "a/alice")
    result = execute_read("alice", "a", ("a", "alice"), {("alice", "a")}, {("a", "alice"): a}, lambda b: b.site)
    assert result == "a.localhost"
```

- [ ] 执行 `.venv/bin/python -m pytest tests/test_read_execution.py -q`，确认失败，再实现：

```python
from dsherp.access import resolve_binding

def execute_read(user_id, enterprise_id, task_owner, memberships, bindings, read):
    if task_owner != (enterprise_id, user_id):
        raise PermissionError("Task ownership mismatch")
    binding = resolve_binding(user_id, enterprise_id, memberships, bindings)
    return read(binding)
```

- [ ] 重跑两个测试文件。用已有 `tests/integration/test_erp_mcp.py` 复跑真实身份错配与权限拒绝，不能用这里的纯函数测试代替真实 ERP 权限。
- [ ] 分类提交，更新已实现范围；不将单站点/本机验证称为多租户 SaaS 安全完成。

## 完成边界

本计划只完成身份绑定后的授权内核。HTTP 认证入口、持久成员库、第二个真实 Site、Runtime 容器隔离、开通任务和写入确认尚需各自的实现及真实验收；不在本计划中预建空平台。继续业务写入前必须先完成这些边界，而不是直接给当前 MCP 加提交工具。
