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
    return {
      key: `${index}:${source.tool}`,
      tool: source.tool,
      label: toolNames[source.tool]?.(source) ?? source.tool,
      detail: fields.length
        ? `${fields.length} 个字段`
        : records.length
          ? `${records.length} 条记录`
          : '',
      doctype: source.arguments?.doctype ?? null,
      records,
      fields,
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
