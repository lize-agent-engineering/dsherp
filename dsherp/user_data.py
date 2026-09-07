"""Exporting and deleting one person's data (T4), inside ruling #10's boundary.

The ruling draws the line at accountability: the facts that say what the assistant did on
whose behalf - the run, the events, what it read, what was proposed, confirmed and executed,
and what became of the document - are retained, because deleting them would destroy the
ability to replay and to hold anyone to account. What goes is the person's own content: the
prompts they typed, the page snapshots that carry values they had not saved, the prose written
back to them, and their native session directories.

Two consequences are stated rather than hidden:
  * an event is never rewritten, so personal text inside an event's payload stays; the only
    way to remove it is a registered, controlled de-identification migration (ruling #10);
  * the version history that track_changes keeps would otherwise hold a copy of every column
    cleared here, so those versions are removed with the columns.

Masking in the interface is not deletion, and nothing here masks."""
import json
import time

FORMAT = 1

# What a cleared page snapshot is replaced by: the Site validates this column against its
# own schema on every read, so an empty string would turn a deleted person's old conversations
# into server errors. A page nobody can look at any more is exactly "unknown".
CLEARED_CONTEXT = ('{"page_type":"unknown","reason":"\u7528\u6237\u6570\u636e\u5df2\u6309\u8bf7\u6c42\u5220\u9664",'
                   '"route":[],"schema_version":1}')

# Per DocType: the columns that hold the person's own content (with what replaces each), the
# facts that stay, and why.
BOUNDARY = {
    'DS Model Run': {
        'clear': {'question': '', 'page_context': CLEARED_CONTEXT, 'answer': '', 'needs_input': '',
                  'error': ''},
        'keep': ('name', 'owner', 'conversation', 'domain', 'status', 'sources', 'request_id',
                 'request_digest', 'model_calls', 'model_input_bytes', 'model', 'actual_input_tokens',
                 'actual_output_tokens', 'duration_ms', 'creation', 'modified'),
        'why': '运行本身是"助手替谁做了什么"的凭据：谁、何时、读了哪些记录、结果如何都保留；'
               '提问、页面快照、回答与错误正文是本人内容，清除。平台授权令牌不在行里（它只在运行期间存于缓存）。',
    },
    'DS Conversation': {
        'clear': {'title': ''},
        'keep': ('name', 'owner', 'archived', 'archived_at', 'creation'),
        'why': '会话标题是用户自己起的名字，清除；会话本身不删，否则它下面的运行就失去上下文。',
    },
    'DS Operation Proposal': {
        'clear': {},
        'keep': ('name', 'owner', 'conversation', 'model_run', 'payload', 'digest', 'status', 'expires_at'),
        'why': '提案是"提出过什么操作"的事实，逐字保留；它的 payload 是操作对象与字段变化，属于审计，不是个人内容。',
    },
    'DS Execution Record': {
        'clear': {},
        'keep': ('name', 'proposal', 'request_id', 'status', 'result', 'target_doctype', 'target_name', 'target_state'),
        'why': '执行结果与它产生的单据是对外可追责的事实，全部保留。',
    },
    'DS Run Event': {
        'clear': {},
        'keep': ('run', 'seq', 'kind', 'source', 'error_class', 'payload', 'recorded_at'),
        'why': '已有事件禁止改写（裁决 #10）；事件里的个人信息只能走登记的受控脱敏迁移。',
    },
}

# What this command knowingly leaves behind, and the only allowed way to remove it.
RESIDUE = (
    {'doctype': 'DS Run Event',
     'what': '事件 payload 里可能含有提问片段、回答片段与页面上下文摘要',
     'why': '裁决 #10：已有事件禁止改写',
     'removal': '只能以明确登记的受控脱敏迁移作为不可变承诺的例外执行'},
    {'doctype': 'Version',
     'what': 'track_changes 为被清除的列留下的历史值',
     'why': '版本记录本身就是"改过什么"的证据',
     'removal': '与被清除的列一起删除（本命令会删这些 Version 行）；此外只能走受控脱敏迁移'},
    {'doctype': '备份集',
     'what': '已生成的异地备份里仍含删除前的数据',
     'why': '备份是不可改写的时点副本',
     'removal': '按保留策略到期淘汰；不为一次删除请求改写历史备份'},
)


def cleared(doctype):
    """The write that empties this DocType's personal columns. Raises for a DocType with none.

    Each column has its own neutral value rather than a blanket empty string: what is left
    behind still has to be something the Site can read."""
    columns = BOUNDARY[doctype]['clear']
    if not columns:
        raise KeyError(f'{doctype} 没有可清除的个人内容列')
    return dict(columns)


def plan(*, user, conversations, runs, proposals, executions, sessions):
    """What a deletion would do, before anything is touched."""
    return {
        'format': FORMAT,
        'user': user,
        'counts': {'conversations': len(conversations), 'runs': len(runs),
                   'proposals': len(proposals), 'executions': len(executions), 'sessions': sessions},
        'clears': {doctype: sorted(rules['clear']) for doctype, rules in BOUNDARY.items() if rules['clear']},
        'keeps': {'runs': len(runs), 'proposals': len(proposals), 'executions': len(executions),
                  'conversations': len(conversations)},
        'untouched': [doctype for doctype, rules in BOUNDARY.items() if not rules['clear']],
        'residue': [dict(item) for item in RESIDUE],
    }


def export_document(*, user, site, conversations, runs, proposals, executions):
    """Everything this deployment holds about one person, as one JSON document."""
    return {
        'format': FORMAT,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'site': site,
        'user': user,
        'conversations': [dict(row) for row in conversations],
        'runs': [dict(row) for row in runs],
        'proposals': [dict(row) for row in proposals],
        'executions': [dict(row) for row in executions],
        'note': '运行事件（DS Run Event）不在本导出内：它是逐条追加的审计流，可按运行单独调取。',
    }
