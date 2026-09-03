// Presentation only. Every item keeps the server's own attribution: an item
// whose originating run is unknown is listed apart rather than pinned to a turn
// it may not belong to.
const toolNames = {
  erp_read_record: (source) => `读取 ${source.arguments?.doctype} / ${source.arguments?.name}`,
  erp_read_schema: (source) => `读取 ${source.arguments?.doctype} 结构`,
  erp_search_records: (source) =>
    source.arguments?.query
      ? `搜索 ${source.arguments?.doctype}：${source.arguments.query}`
      : `搜索 ${source.arguments?.doctype}`,
  erp_read_configuration: (source) => `读取配置 ${source.arguments?.doctype}`,
  erp_propose_configuration: () => '提出配置提案',
};

export function toolEvents(sources = []) {
  return (sources ?? []).map((source, index) => {
    const records = source.records ?? [];
    const fields = source.fields ?? [];
    const modules = source.modules ?? [];
    const roles = source.roles ?? [];
    return {
      key: `${index}:${source.tool}`,
      step: index + 1,
      tool: source.tool,
      label: toolNames[source.tool]?.(source) ?? source.tool,
      detail: [
        fields.length ? `${fields.length} 个字段` : '',
        records.length ? `${records.length} 条记录` : '',
        modules.length ? `${modules.length} 个模块` : '',
        roles.length ? `${roles.length} 个角色` : '',
      ]
        .filter(Boolean)
        .join(' · '),
      doctype: source.arguments?.doctype ?? null,
      arguments: source.arguments ?? null,
      records,
      fields,
      modules,
      roles,
      // Baselines are passed through as the server recorded them; how they are
      // worded is the presentation layer's business.
      schemaVersion: source.schema_version ?? null,
      recordVersions: source.record_versions ?? null,
      exists: source.exists ?? null,
      configVersion: source.version ?? null,
      configRevision: source.configuration_revision ?? null,
    };
  });
}

export function buildTranscript(session) {
  const empty = { proposals: [], bundles: [], confirmations: [] };
  const runs = session?.messages ?? [];
  const turns = runs.map((message) => ({
    message,
    tools: toolEvents(message.sources),
    proposals: [],
    bundles: [],
    confirmations: [],
  }));
  const loose = { proposals: [], bundles: [], confirmations: [] };
  if (!runs.length) return { turns, loose };
  const at = new Map(runs.map((message, index) => [message.id, index]));
  const place = (item, key, runId) => {
    const index = at.get(runId);
    if (index === undefined) loose[key].push(item);
    else turns[index][key].push(item);
  };
  for (const proposal of session?.proposals ?? []) place(proposal, 'proposals', proposal.model_run);
  const bundles = session?.configuration_bundles ?? [];
  for (const bundle of bundles) place(bundle, 'bundles', bundle.model_run);
  const bundleRun = new Map(bundles.map((bundle) => [bundle.id, bundle.model_run]));
  for (const confirmation of session?.configuration_confirmations ?? [])
    place(confirmation, 'confirmations', bundleRun.get(confirmation.bundle_id));
  return { turns, loose: { ...empty, ...loose } };
}

export const pendingCount = (session) =>
  (session?.proposals ?? []).filter((item) => item.status === 'Pending').length +
    (session?.configuration_confirmations ?? []).filter((item) => item.status === 'Pending').length;

const eventTones = new Set(['model_error', 'tool_error', 'runtime_failed', 'worker_error']);

function eventDetail(event, payload) {
  const bits = [];
  if (event.error_class) bits.push(event.error_class);
  if (payload == null) return bits.join(' ');
  if (typeof payload !== 'object') {
    bits.push(String(payload));
    return bits.join(' ');
  }
  if (typeof payload.text === 'string' && payload.text) bits.push(payload.text);
  else if (typeof payload.error === 'string' && payload.error) bits.push(payload.error);
  else if (Object.keys(payload).length) bits.push(JSON.stringify(payload));
  return bits.join(' ');
}

function eventLabel(event, payload, reserved) {
  switch (event.kind) {
    case 'queued':
      return '已排队';
    case 'claimed':
      return '已领取';
    case 'runtime_started':
      return '运行时启动';
    case 'model_call_reserved':
      return `模型调用 #${payload.model_calls ?? payload.call_index ?? reserved}`;
    case 'model_response':
      return '模型返回';
    case 'model_error':
      return '模型错误';
    case 'runtime_tool_call':
      return `调用工具 ${payload.tool}`;
    case 'tool_call':
      return `服务端执行 ${payload.tool}（${payload.duration_ms} ms）`;
    case 'tool_result':
      return '工具返回';
    case 'tool_error':
      return '工具错误';
    case 'compaction':
      return '上下文压缩';
    case 'turn_end':
      return `回合结束（${payload.reason}）`;
    case 'runtime_failed':
      return `运行失败（${event.error_class}）`;
    case 'container_finished':
      return `容器结束（${payload.status}）`;
    case 'finished':
      return `运行结束（${payload.status}）`;
    case 'expired':
      return '运行过期';
    case 'cancel_requested':
      return '已请求取消';
    case 'worker_error':
      return 'worker 错误';
    default:
      return event.kind;
  }
}

export function runEventRows(events) {
  let reserved = 0;
  return events.map((event) => {
    const payload = event.payload ?? {};
    if (event.kind === 'model_call_reserved') reserved += 1;
    return {
      seq: event.seq,
      time: event.recorded_at,
      label: eventLabel(event, payload, reserved),
      detail: eventDetail(event, event.payload),
      tone: eventTones.has(event.kind) ? 'danger' : 'default',
    };
  });
}
