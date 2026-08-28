// Stage 2 only: no network, storage, credentials or real ERP mutations.
export const tenants = { alpha: "演示甲企业", beta: "演示乙企业" };
export const roles = {
  employee: "业务员工",
  designer: "应用设计者",
  publisher: "企业发布者",
  operator: "平台运维",
};
export const outcomes = {
  success: {
    label: "演示发布完成",
    detail: "候选版本已在本页演示状态生效；没有向 ERP 安装或发布。",
    completed: ["创建演示字段", "配置演示权限", "登记演示菜单"],
  },
  failed: {
    label: "演示发布失败",
    detail: "制品校验失败，目标未变更。检查测试结果，修改后重新预览确认。",
    completed: [],
  },
  conflict: {
    label: "迁移 / 配置冲突",
    detail:
      "存在无法转换的费用值“待核价”，或目标配置已被原生后台修改。停止发布，先核对差异；不覆盖。",
    completed: [],
  },
  partial: {
    label: "部分成功",
    detail:
      "演示字段创建完成，权限同步失败；菜单未开放。核实已完成步骤后制定修复，不承诺自动回滚。",
    completed: ["创建演示字段"],
  },
  unknown: {
    label: "结果待核实",
    detail: "部署响应超时，当前结果不明。先查询实际版本，不重复部署或迁移。",
    completed: [],
  },
};
export function initialState(tenant = "alpha", role = "publisher") {
  return {
    tenant,
    role,
    page: "work",
    sessionValid: true,
    requirement:
      "建立设备维修应用：员工报修，主管分配，维修员处理；费用超过 500 元需主管审批。",
    revision: 1,
    targetVersion: 0,
    candidate: null,
    confirmation: null,
    result: null,
    publishedVersion: 0,
    history: [],
    repairs: [],
  };
}
export function canAccess(state, page) {
  if (!state.sessionValid) return false;
  if (page === "platform") return state.role === "operator";
  if (state.role === "operator") return page === "account";
  if (["build", "catalog", "releases"].includes(page))
    return ["designer", "publisher"].includes(state.role);
  if (page === "settings") return state.role === "publisher";
  if (page === "repairs") return state.publishedVersion > 0;
  return ["work", "assistant", "todos", "tasks", "account"].includes(page);
}
function requirePublisher(state) {
  if (state.role !== "publisher")
    throw new Error("缺少发布权限；请由企业发布者确认。");
}
function requireKnown(state) {
  if (["unknown", "partial"].includes(state.result?.status))
    throw new Error("必须先核实现有任务，不得重复发布。");
}
function finish(state, status) {
  const result = {
    status,
    ...outcomes[status],
    version: state.candidate.version,
  };
  return {
    ...state,
    confirmation: null,
    result,
    publishedVersion:
      status === "success" ? state.candidate.version : state.publishedVersion,
    targetVersion:
      status === "success" ? state.targetVersion + 1 : state.targetVersion,
    history: [{ ...result, id: state.history.length + 1 }, ...state.history],
  };
}
export function transition(state, event) {
  if (!state.sessionValid) throw new Error("演示会话已失效，请重新进入。");
  switch (event.type) {
    case "tenant":
      if (!tenants[event.tenant]) throw new Error("企业不存在");
      return initialState(event.tenant, state.role);
    case "role":
      if (!roles[event.role]) throw new Error("角色不存在");
      return { ...state, role: event.role, confirmation: null };
    case "navigate":
      return { ...state, page: event.page };
    case "expire":
      return {
        ...initialState(state.tenant, state.role),
        sessionValid: false,
        requirement: "",
      };
    case "edit":
      requireKnown(state);
      if (state.role === "operator")
        throw new Error("平台角色不能编辑企业需求");
      return {
        ...state,
        requirement: event.value,
        revision: state.revision + 1,
        candidate: null,
        confirmation: null,
        result: null,
      };
    case "preview":
      requireKnown(state);
      if (state.revision <= state.publishedVersion)
        throw new Error("此候选已发布，请先修改需求再生成新版本");
      if (!canAccess(state, "build")) throw new Error("缺少构建权限");
      if (!state.requirement.trim()) throw new Error("请先填写需求");
      return {
        ...state,
        candidate: {
          version: state.revision,
          requirement: state.requirement,
          targetVersion: state.targetVersion,
        },
        confirmation: null,
        result: null,
      };
    case "confirm":
      requirePublisher(state);
      if (state.revision <= state.publishedVersion)
        throw new Error("此候选已发布，请先修改需求再生成新版本");
      requireKnown(state);
      if (!state.candidate) throw new Error("请先预览具体候选版本");
      return {
        ...state,
        confirmation: {
          tenant: state.tenant,
          role: state.role,
          version: state.candidate.version,
        },
      };
    case "targetChanged":
      return { ...state, targetVersion: state.targetVersion + 1 };
    case "publish":
      requirePublisher(state);
      requireKnown(state);
      if (
        !state.confirmation ||
        !state.candidate ||
        state.confirmation.version !== state.candidate.version ||
        state.confirmation.tenant !== state.tenant
      )
        throw new Error("请重新确认具体版本");
      if (!Object.hasOwn(outcomes, event.outcome))
        throw new Error("未知的演示结果");
      return finish(
        state,
        state.targetVersion !== state.candidate.targetVersion
          ? "conflict"
          : event.outcome,
      );
    case "reconcile": {
      requirePublisher(state);
      if (state.result?.status !== "unknown")
        throw new Error("当前没有结果不明的发布任务");
      // Explicit scenario: readback says the same candidate is installed, not a retry.
      const resolved = finish(
        { ...state, history: state.history.slice(1) },
        "success",
      );
      return {
        ...resolved,
        result: {
          ...resolved.result,
          detail: "模拟核实返回：候选版本已生效。本操作没有再次执行发布。",
        },
      };
    }
    case "repair":
      if (!canAccess(state, "repairs") || state.role === "designer")
        throw new Error("缺少业务录入权限");
      if (!event.equipment?.trim() || !event.description?.trim())
        throw new Error("设备与故障描述不能为空");
      return {
        ...state,
        repairs: [
          {
            id: `DEMO-${state.repairs.length + 1}`,
            equipment: event.equipment.trim(),
            description: event.description.trim(),
            status: "待分配",
          },
          ...state.repairs,
        ],
      };
    default:
      throw new Error("未知操作");
  }
}
