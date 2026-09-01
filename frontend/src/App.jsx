import React, { useState } from "react";
import {
  Alert,
  Button,
  ConfigProvider,
  Descriptions,
  Empty,
  Form,
  Input,
  Layout,
  Menu,
  Modal,
  Result,
  Select,
  Space,
  Steps,
  Table,
  Tabs,
  Tag,
} from "antd";
import zhCN from "antd/locale/zh_CN.js";
import {
  canAccess,
  initialState,
  outcomes,
  roles,
  tenants,
  transition,
} from "./model.js";
import "./style.css";

const labels = {
  work: "工作台",
  assistant: "Agent 助手",
  todos: "我的待办",
  tasks: "任务记录",
  repairs: "设备维修",
  catalog: "应用目录",
  build: "构建工作区",
  releases: "发布与变更",
  settings: "企业设置",
  platform: "平台管理",
  account: "账号与企业",
};
const columns = (fields) =>
  fields.map(([dataIndex, title]) => ({ dataIndex, title, key: dataIndex }));
const fields = [
  {
    key: "equipment",
    name: "设备",
    type: "Link",
    change: "新增关联",
    note: "拟复用原生设备对象；阶段 3 核对实际字段",
  },
  {
    key: "description",
    name: "故障描述",
    type: "Small Text",
    change: "新增必填",
    note: "员工填写",
  },
  {
    key: "status",
    name: "处理状态",
    type: "Select",
    change: "新增状态",
    note: "待分配 → 维修中 → 待验收 → 完成",
  },
  {
    key: "cost",
    name: "维修费用",
    type: "Currency",
    change: "新增金额",
    note: "超过 500 元进入主管审批",
  },
];
function DataTable({ data, fields: tableFields }) {
  return (
    <Table
      rowKey="key"
      size="small"
      pagination={false}
      columns={columns(tableFields)}
      dataSource={data}
    />
  );
}
function Heading({ title, children, action }) {
  return (
    <div className="dsh-heading">
      <div>
        <h2>{title}</h2>
        {children && <p>{children}</p>}
      </div>
      {action}
    </div>
  );
}
function History({ state }) {
  return (
    <Table
      rowKey="id"
      pagination={false}
      size="small"
      locale={{ emptyText: "尚无演示发布任务" }}
      columns={[
        {
          title: "任务",
          dataIndex: "id",
          render: (id) => `DEMO-PUBLISH-${id}`,
        },
        { title: "候选版本", dataIndex: "version", render: (v) => `v${v}` },
        { title: "实际演示结果", dataIndex: "label" },
        {
          title: "已完成步骤",
          dataIndex: "completed",
          render: (items) => items.join("、") || "无已确认变更",
        },
      ]}
      dataSource={state.history}
    />
  );
}
function ReleaseResult({ state, send }) {
  const r = state.result;
  if (!r) return null;
  return (
    <section className="dsh-section" aria-live="polite">
      <Alert
        showIcon
        type={
          r.status === "success"
            ? "success"
            : r.status === "failed"
              ? "error"
              : "warning"
        }
        message={r.label}
        description={r.detail}
      />
      <p>
        已确认完成：{r.completed.join("、") || "无"}。没有承诺跨步骤自动回滚。
      </p>
      {r.status === "success" && (
        <Button
          type="primary"
          onClick={() => send({ type: "navigate", page: "repairs" })}
        >
          打开演示业务页面
        </Button>
      )}
      {r.status === "unknown" && state.role === "publisher" && (
        <Button onClick={() => send({ type: "reconcile" })}>
          模拟核实：版本已生效
        </Button>
      )}
      {r.status === "partial" && (
        <p>
          下一步：查看任务记录，交由发布者核对权限同步失败；本原型不执行修复或恢复。
        </p>
      )}
    </section>
  );
}
function Preview() {
  const [role, setRole] = useState("employee");
  return (
    <div className="dsh-section">
      <Space>
        <Tag color="blue">合成预览</Tag>
        <Select
          aria-label="预览业务角色"
          value={role}
          onChange={setRole}
          options={[
            { value: "employee", label: "报修员工" },
            { value: "manager", label: "维修主管" },
          ]}
        />
      </Space>
      <Heading title="设备维修 / 报修记录">
        拟生成原生列表与表单；以下为 React 交互样板，不是已创建的 DocType。
      </Heading>
      <DataTable
        data={[
          {
            key: "DEMO-001",
            device: "演示空压机",
            problem: "运行时异响",
            status: "待分配",
            scope: role === "employee" ? "仅本人报修" : "本部门报修",
          },
        ]}
        fields={[
          ["device", "设备"],
          ["problem", "故障"],
          ["status", "状态"],
          ["scope", "可见范围"],
        ]}
      />
      <p>
        {role === "employee"
          ? "员工能新增报修，不能分配人员、审核费用或修改结构。"
          : "主管可分配维修任务、审核费用；不因此取得应用发布权限。"}
      </p>
    </div>
  );
}
function Build({ state, send }) {
  const [tab, setTab] = useState("requirements");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [outcome, setOutcome] = useState("success");
  const blocked = ["partial", "unknown"].includes(state.result?.status);
  const candidate = state.candidate;
  const tabs = [
    {
      key: "requirements",
      label: "需求与方案",
      children: (
        <div className="dsh-build-grid">
          <section className="dsh-section">
            <h3>描述业务需要</h3>
            <label htmlFor="requirement">业务需求</label>
            <Input.TextArea
              id="requirement"
              aria-label="业务需求"
              rows={6}
              value={state.requirement}
              disabled={blocked}
              onChange={(e) => send({ type: "edit", value: e.target.value })}
            />
            <p className="dsh-muted">
              固定设备维修样例。输入会保存为需求备注；本阶段不调用模型理解或生成任意应用。
            </p>
            <Button
              type="primary"
              disabled={blocked}
              onClick={() => {
                if (send({ type: "preview" })) setTab("pages");
              }}
            >
              生成演示方案
            </Button>
          </section>
          <section className="dsh-section">
            <h3>优先复用，再扩展</h3>
            <ol className="dsh-outline">
              <li>
                <b>业务对象</b>
                <p>关联已有设备，新增报修记录与维修明细。</p>
              </li>
              <li>
                <b>业务规则</b>
                <p>原生 Workflow 承担分配和费用审批；不重写库存、账务规则。</p>
              </li>
              <li>
                <b>验收场景</b>
                <p>员工不能分配任务；主管审批费用；未验收不能完成。</p>
              </li>
            </ol>
          </section>
        </div>
      ),
    },
    { key: "pages", label: "页面与导航", children: <Preview /> },
    {
      key: "schema",
      label: "数据结构",
      children: (
        <section className="dsh-section">
          <h3>候选字段 · 尚未创建</h3>
          <DataTable
            data={fields}
            fields={[
              ["name", "字段"],
              ["type", "原生类型"],
              ["change", "变化"],
              ["note", "说明"],
            ]}
          />
          <Alert
            type="info"
            message="不复制一套业务模型"
            description="候选包计划使用 Frappe DocType / Workspace；实际对象、字段与权限需在真实接入阶段发现和核验。"
          />
        </section>
      ),
    },
    {
      key: "rules",
      label: "业务规则",
      children: (
        <section className="dsh-section">
          <h3>维修处理流程</h3>
          <Steps
            size="small"
            current={1}
            items={["报修", "分配", "维修", "验收"].map((title) => ({ title }))}
          />
          <DataTable
            data={[
              {
                key: "approval",
                rule: "费用 > 500 元",
                implementation: "原生 Workflow 条件与角色审批",
                test: "员工直接完成必须拒绝",
              },
              {
                key: "finish",
                rule: "验收通过才可完成",
                implementation: "原生工作流状态转换",
                test: "跳过验收必须拒绝",
              },
              {
                key: "cost",
                rule: "材料费 + 工时费",
                implementation: "必要时生成自定义 App 计算与测试",
                test: "负数及非法金额必须拒绝",
              },
            ]}
            fields={[
              ["rule", "规则"],
              ["implementation", "拟采用方式"],
              ["test", "验收行为"],
            ]}
          />
        </section>
      ),
    },
    {
      key: "permissions",
      label: "角色权限",
      children: (
        <section className="dsh-section">
          <h3>业务权限与发布权限分开</h3>
          <p>
            为走通样例，本页“企业发布者”同时被赋予业务员工与应用设计者能力；真实系统必须分别授权。
          </p>
          <DataTable
            data={[
              {
                key: "employee",
                role: "报修员工",
                read: "本人记录",
                write: "新建与修改本人草稿",
                publish: "无",
              },
              {
                key: "manager",
                role: "维修主管",
                read: "本部门记录",
                write: "分配、审批与验收",
                publish: "无",
              },
              {
                key: "publisher",
                role: "企业发布者",
                read: "另按业务角色授权",
                write: "不默认获得业务写入",
                publish: "确认具体应用版本",
              },
            ]}
            fields={[
              ["role", "角色"],
              ["read", "记录范围"],
              ["write", "业务动作"],
              ["publish", "应用发布"],
            ]}
          />
          <p>
            演示按钮显隐不是安全边界。阶段 3
            起由服务端对直接请求、字段与动作逐一检查。
          </p>
        </section>
      ),
    },
    {
      key: "tests",
      label: "测试预览",
      children: (
        <section className="dsh-section">
          <h3>待执行的隔离验收场景</h3>
          <DataTable
            data={[
              {
                key: "role",
                case: "员工打开他人报修",
                expected: "服务端拒绝",
                status: "未执行 · 样例",
              },
              {
                key: "cost",
                case: "费用 -1 或非法字符串",
                expected: "业务校验拒绝",
                status: "未执行 · 样例",
              },
              {
                key: "migration",
                case: "费用备注转金额：“待核价”",
                expected: "报告冲突，停止迁移",
                status: "未执行 · 样例",
              },
              {
                key: "version",
                case: "确认后原生配置被改动",
                expected: "版本冲突，重新预览",
                status: "未执行 · 样例",
              },
            ]}
            fields={[
              ["case", "场景"],
              ["expected", "预期"],
              ["status", "真实验证状态"],
            ]}
          />
          <p>这些是未来生成应用的验收计划，不是已运行的业务测试报告。</p>
        </section>
      ),
    },
    {
      key: "release",
      label: "版本与发布",
      children: (
        <section className="dsh-section">
          <Heading
            title={candidate ? `候选 v${candidate.version}` : "尚无候选版本"}
          >
            确认绑定本企业、发布者、候选内容与目标版本。修改内容后须重新预览。
          </Heading>
          {candidate ? (
            <>
              <Descriptions
                size="small"
                column={2}
                items={[
                  {
                    key: "tenant",
                    label: "演示目标",
                    children: tenants[state.tenant],
                  },
                  {
                    key: "version",
                    label: "目标配置版本",
                    children: `r${state.targetVersion}`,
                  },
                  {
                    key: "impact",
                    label: "新增内容",
                    children: "报修对象、4 个字段、角色权限、维修菜单",
                  },
                  {
                    key: "data",
                    label: "迁移影响",
                    children: "当前新增样例不迁移旧数据；冲突场景仅演示",
                  },
                ]}
              />
              <p className="dsh-quote">{candidate.requirement}</p>
              <Space wrap>
                <Select
                  aria-label="演示发布结果"
                  value={outcome}
                  onChange={setOutcome}
                  options={Object.entries(outcomes).map(([value, r]) => ({
                    value,
                    label: r.label,
                  }))}
                  style={{ width: 190 }}
                />
                {!state.confirmation &&
                  state.role === "publisher" &&
                  state.result?.status !== "success" &&
                  !blocked && (
                    <Button type="primary" onClick={() => setConfirmOpen(true)}>
                      确认候选版本
                    </Button>
                  )}
                {state.confirmation && !blocked && (
                  <Button
                    type="primary"
                    onClick={() => send({ type: "publish", outcome })}
                  >
                    执行演示发布
                  </Button>
                )}
                {!blocked && (
                  <Button onClick={() => send({ type: "targetChanged" })}>
                    模拟原生后台配置变化
                  </Button>
                )}
              </Space>
              {state.role !== "publisher" && (
                <Alert
                  type="warning"
                  message="当前设计者没有发布权限，请交由企业发布者确认。"
                />
              )}
              <ReleaseResult state={state} send={send} />
            </>
          ) : (
            <Button onClick={() => setTab("requirements")}>
              先描述需求并生成方案
            </Button>
          )}
        </section>
      ),
    },
  ];
  return (
    <>
      <Heading title="设备维修" action={<Tag color="blue">构建中 · 演示</Tag>}>
        把需求变成可预览的业务应用，再确认具体变更。
      </Heading>
      <Steps
        className="dsh-progress"
        size="small"
        current={
          state.result?.status === "success"
            ? 3
            : state.confirmation
              ? 2
              : candidate
                ? 1
                : 0
        }
        items={["描述需求", "查看预览", "确认版本", "发布结果"].map(
          (title) => ({ title }),
        )}
      />
      <Tabs activeKey={tab} onChange={setTab} items={tabs} />
      <Modal
        title="确认应用发布 · 演示"
        open={confirmOpen}
        destroyOnHidden
        onCancel={() => setConfirmOpen(false)}
        okText="确认此版本（演示）"
        cancelText="继续检查"
        onOk={() => {
          if (send({ type: "confirm" })) setConfirmOpen(false);
        }}
      >
        <p>
          企业：{tenants[state.tenant]}；发布者：{roles[state.role]}；候选：v
          {candidate?.version}。
        </p>
        <p>将登记维修字段、权限和菜单。候选内容变更后此确认失效。</p>
        <Alert
          type="warning"
          showIcon
          message="仅确认本页演示状态，不安装 App、不迁移数据、不写入 ERP。"
        />
      </Modal>
    </>
  );
}
function Repairs({ state, send }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Heading
        title="报修记录"
        action={
          state.role !== "designer" && (
            <Button type="primary" onClick={() => setOpen(true)}>
              新增演示报修
            </Button>
          )
        }
      >
        设备维修 · 演示 v{state.publishedVersion}
        。数据仅保存在当前页面，切换企业或刷新即清空。
      </Heading>
      <Table
        rowKey="id"
        size="small"
        dataSource={state.repairs}
        columns={columns([
          ["id", "编号"],
          ["equipment", "设备"],
          ["description", "故障描述"],
          ["status", "状态"],
        ])}
        locale={{ emptyText: "还没有演示报修，新增一条试用表单。" }}
      />
      <Modal
        open={open}
        title="新增演示报修"
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Form
          layout="vertical"
          preserve={false}
          onFinish={(values) => {
            if (send({ type: "repair", ...values })) setOpen(false);
          }}
        >
          <Form.Item
            name="equipment"
            label="设备名称"
            rules={[
              { required: true, whitespace: true, message: "请输入设备名称" },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="description"
            label="故障描述"
            rules={[
              { required: true, whitespace: true, message: "请输入故障描述" },
            ]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
          <Button htmlType="submit" type="primary">
            保存到本页演示
          </Button>
        </Form>
      </Modal>
    </>
  );
}
function Platform() {
  const tasks = [
    {
      key: "PROV-001",
      tenant: "演示甲企业",
      task: "Site 开通",
      status: "演示：完成",
      detail: "技术步骤：创建站点 → 安装应用 → 健康检查",
    },
    {
      key: "PROV-002",
      tenant: "演示乙企业",
      task: "版本部署",
      status: "演示：失败",
      detail: "制品校验不通过；尚未写入目标环境",
    },
  ];
  return (
    <>
      <Heading title="平台管理">
        仅技术状态与运维操作，不读取客户业务单据。以下资源和任务均为合成示例。
      </Heading>
      <Tabs
        items={[
          {
            key: "tenants",
            label: "租户管理",
            children: (
              <DataTable
                data={tasks}
                fields={[
                  ["tenant", "企业"],
                  ["status", "开通 / 服务状态"],
                  ["detail", "技术详情"],
                ]}
              />
            ),
          },
          {
            key: "jobs",
            label: "开通与部署任务",
            children: (
              <DataTable
                data={tasks}
                fields={[
                  ["key", "任务"],
                  ["tenant", "企业"],
                  ["task", "类型"],
                  ["status", "状态"],
                  ["detail", "失败步骤 / 结果"],
                ]}
              />
            ),
          },
          {
            key: "resources",
            label: "运行与资源",
            children: (
              <Alert
                type="info"
                message="实时资源尚未接入"
                description="本原型不生成虚假实时指标。后续按租户显示服务、资源与 Agent 异常，不展示客户业务内容。"
              />
            ),
          },
          {
            key: "members",
            label: "平台成员与权限",
            children: (
              <DataTable
                data={[
                  {
                    key: "ops",
                    role: "运维",
                    scope: "技术部署与服务状态",
                    business: "不默认授予企业业务权限",
                  },
                ]}
                fields={[
                  ["role", "平台角色"],
                  ["scope", "职责"],
                  ["business", "边界"],
                ]}
              />
            ),
          },
          {
            key: "audit",
            label: "发布与操作记录",
            children: (
              <DataTable
                data={tasks}
                fields={[
                  ["key", "操作"],
                  ["task", "技术动作"],
                  ["status", "结果"],
                  ["detail", "已知状态"],
                ]}
              />
            ),
          },
        ]}
      />
    </>
  );
}
function Assistant({ state, send }) {
  const [question, setQuestion] = useState("查询我的待处理维修任务");
  const [answer, setAnswer] = useState(false);
  return (
    <>
      <Heading title="Agent 助手">
        真实 DSH 查询将在阶段 3 接入。当前仅展示一个固定交互样例。
      </Heading>
      <section className="dsh-section">
        <label htmlFor="agent-input">向助手提问</label>
        <Input.TextArea
          id="agent-input"
          rows={3}
          value={question}
          onChange={(e) => {
            setQuestion(e.target.value);
            setAnswer(false);
          }}
        />
        <Button
          className="dsh-spaced"
          type="primary"
          disabled={!question.trim()}
          onClick={() => setAnswer(true)}
        >
          查看演示回答
        </Button>
        {answer && (
          <div className="dsh-answer">
            <Tag>固定样例 · 未调用 Agent</Tag>
            <p>
              演示回答：查询应使用当前企业、当前用户权限。这里没有查询真实数据，也没有执行任何业务动作。
            </p>
            <Button onClick={() => send({ type: "navigate", page: "todos" })}>
              查看演示待办来源
            </Button>
          </div>
        )}
      </section>
    </>
  );
}
function Content({ state, send }) {
  const go = (page) => () => send({ type: "navigate", page });
  if (!canAccess(state, state.page))
    return (
      <Result
        status="403"
        title="无权访问此空间"
        subTitle="当前演示角色无此能力；真实权限仍由 Frappe 服务端控制。"
        extra={
          <Button onClick={go(state.role === "operator" ? "platform" : "work")}>
            返回可用空间
          </Button>
        }
      />
    );
  switch (state.page) {
    case "build":
      return <Build state={state} send={send} />;
    case "repairs":
      return <Repairs state={state} send={send} />;
    case "platform":
      return <Platform />;
    case "assistant":
      return <Assistant state={state} send={send} />;
    case "work":
      return (
        <>
          <Heading title="工作台">
            {tenants[state.tenant]} · 从待处理事项进入工作，不堆砌汇总数字。
          </Heading>
          <div className="dsh-build-grid">
            <section className="dsh-section">
              <Tag color="blue">应用构建</Tag>
              <h3>把设备维修流程变成应用</h3>
              <p>
                描述报修、分配、费用审批和验收规则，先查看效果，再确认发布。
              </p>
              <Button type="primary" onClick={go("build")}>
                {canAccess(state, "build") ? "开始构建" : "查看构建权限"}
              </Button>
            </section>
            <section className="dsh-section">
              <h3>继续业务工作</h3>
              <p>助手查询与待办按企业、角色划分。原生单据仍在 Desk 中处理。</p>
              <Space wrap>
                <Button onClick={go("assistant")}>打开助手</Button>
                <Button href="/desk/item">原生物料列表</Button>
              </Space>
            </section>
          </div>
          <section className="dsh-section">
            <h3>业务应用</h3>
            {state.publishedVersion ? (
              <Button onClick={go("repairs")}>
                设备维修 · 演示 v{state.publishedVersion}
              </Button>
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="尚未发布演示应用。完成构建流程后，这里会出现业务入口。"
              />
            )}
          </section>
        </>
      );
    case "catalog":
      return (
        <>
          <Heading
            title="应用目录"
            action={
              <Button type="primary" onClick={go("build")}>
                打开设备维修样例
              </Button>
            }
          >
            构建中的候选与已发布版本分开展示。
          </Heading>
          <DataTable
            data={[
              {
                key: "repair",
                app: "设备维修",
                candidate: state.candidate
                  ? `v${state.candidate.version}`
                  : "未生成",
                published: state.publishedVersion
                  ? `演示 v${state.publishedVersion}`
                  : "未发布",
              },
            ]}
            fields={[
              ["app", "应用"],
              ["candidate", "候选"],
              ["published", "已发布"],
            ]}
          />
        </>
      );
    case "releases":
      return (
        <>
          <Heading
            title="发布与变更"
            action={<Button onClick={go("build")}>打开构建工作区</Button>}
          >
            内容与权限确认、并行版本冲突、结果核实在应用内处理。
          </Heading>
          <History state={state} />
        </>
      );
    case "tasks":
      return (
        <>
          <Heading title="任务记录">
            只报告已知步骤；结果不明与部分成功不会显示为成功。
          </Heading>
          <History state={state} />
        </>
      );
    case "todos":
      return (
        <>
          <Heading title="我的待办">
            明确区分来源与确认类型。以下为样例，不会触发真实业务操作。
          </Heading>
          <DataTable
            data={[
              {
                key: "workflow",
                source: "Frappe 工作流 · 演示",
                task: "审核维修费用 680 元",
                action: "业务审批，不是应用发布",
              },
              {
                key: "assignment",
                source: "Frappe 分配 · 演示",
                task: "检查演示空压机",
                action: "业务任务",
              },
              {
                key: "agent",
                source: "Agent 执行前确认 · 演示",
                task: "确认创建维修草稿",
                action: "绑定具体内容与单据版本",
              },
            ]}
            fields={[
              ["source", "来源"],
              ["task", "待办"],
              ["action", "确认范围"],
            ]}
          />
        </>
      );
    case "settings":
      return (
        <>
          <Heading title="企业设置">
            复用原生用户、角色和权限管理。演示角色不会授予真实管理权限。
          </Heading>
          <Space wrap>
            <Button href="/desk/user">用户管理</Button>
            <Button href="/desk/role">角色管理</Button>
            <Button href="/desk/permission-manager">权限配置</Button>
            <Button href="/desk/workflow">工作流</Button>
          </Space>
        </>
      );
    case "account":
      return (
        <>
          <Heading title="账号与企业">
            登录、找回密码和个人安全设置复用 Frappe，不建立第二套密码系统。
          </Heading>
          <Descriptions
            items={[
              {
                key: "tenant",
                label: "演示企业",
                children: tenants[state.tenant],
              },
              { key: "role", label: "演示角色", children: roles[state.role] },
            ]}
          />
          <Space>
            <Button href="/desk/user-profile">原生个人设置</Button>
            <Button onClick={() => send({ type: "expire" })}>
              模拟会话失效
            </Button>
          </Space>
          <section className="dsh-section">
            <h3>企业开通与邀请</h3>
            <p>
              阶段 3
              接入成员关系与企业开通状态；当前企业切换只是本页合成场景，不能进入第二个真实
              Site。
            </p>
            <Alert
              type="info"
              message="创建企业、接受邀请尚未接入，不发送邀请或创建站点。"
            />
          </section>
        </>
      );
    default:
      return <Result status="404" title="页面不存在" />;
  }
}
export default function App() {
  const [state, setState] = useState(() => initialState());
  const [error, setError] = useState("");
  function send(event) {
    try {
      const next = transition(state, event);
      setState(next);
      setError("");
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    }
  }
  const menus = [
    "work",
    "assistant",
    "todos",
    "tasks",
    "repairs",
    "catalog",
    "build",
    "releases",
    "settings",
    "platform",
    "account",
  ]
    .filter((key) => canAccess(state, key))
    .map((key) => ({ key, label: labels[key] }));
  return (
    <ConfigProvider
      locale={zhCN}
      prefixCls="dsh-ant"
      theme={{
        token: {
          colorPrimary: "#176b63",
          colorInfo: "#176b63",
          borderRadius: 6,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
        },
        components: {
          Layout: { bodyBg: "#fff", siderBg: "#f7f9f9" },
          Menu: { itemBg: "#f7f9f9" },
        },
      }}
    >
      <div className="dsherp-prototype">
        <Alert
          banner
          showIcon
          type="info"
          message="交互原型 · 全部为合成数据，不调用 Agent、不写入 ERP、不执行真实发布。刷新、离开本页或切换企业会清空演示状态。"
        />
        {!state.sessionValid ? (
          <Result
            status="warning"
            title="演示会话已失效"
            subTitle="本页状态已清空；真实会话失效时应回到 Frappe 登录。"
            extra={
              <Space>
                <Button
                  onClick={() => {
                    setState(initialState());
                    setError("");
                  }}
                >
                  重新进入演示
                </Button>
                <Button href="/login">原生登录入口</Button>
              </Space>
            }
          />
        ) : (
          <>
            <header className="dsh-context">
              <div className="dsh-wordmark">
                dsherp <span>业务应用</span>
              </div>
              <Space wrap>
                <label htmlFor="demo-tenant">企业</label>
                <Select
                  id="demo-tenant"
                  aria-label="演示企业"
                  value={state.tenant}
                  onChange={(tenant) => send({ type: "tenant", tenant })}
                  options={Object.entries(tenants).map(([value, label]) => ({
                    value,
                    label,
                  }))}
                  style={{ width: 160 }}
                />
                <label htmlFor="demo-role">演示角色</label>
                <Select
                  id="demo-role"
                  aria-label="演示角色"
                  value={state.role}
                  onChange={(role) => send({ type: "role", role })}
                  options={Object.entries(roles).map(([value, label]) => ({
                    value,
                    label,
                  }))}
                  style={{ width: 145 }}
                />
              </Space>
            </header>
            <Layout>
              <Layout.Sider width={176} theme="light">
                <Menu
                  mode="inline"
                  selectedKeys={[state.page]}
                  items={menus}
                  onClick={({ key }) => send({ type: "navigate", page: key })}
                />
                <p className="dsh-sidebar-note">
                  原生 Desk 继续承载业务表单与基础管理。
                </p>
              </Layout.Sider>
              <Layout.Content>
                <div className="dsh-main">
                  {error && <Alert showIcon type="error" message={error} />}
                  <Content
                    key={`${state.tenant}:${state.role}:${state.page}`}
                    state={state}
                    send={send}
                  />
                </div>
              </Layout.Content>
            </Layout>
          </>
        )}
      </div>
    </ConfigProvider>
  );
}
