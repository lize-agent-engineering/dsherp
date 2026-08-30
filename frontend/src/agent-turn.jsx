import React from 'react';
import { LinkOutlined, SafetyCertificateOutlined, SettingOutlined, SwapOutlined, WarningFilled } from '@ant-design/icons';
import { ConfirmCard, Prose, Spark, StatusChip, ToolChain } from './agent-ui.jsx';
import OperationProposal from './OperationProposal.jsx';
import ConfigurationProposal from './ConfigurationProposal.jsx';
import ConfigurationBundle from './ConfigurationBundle.jsx';

// One rendering of a transcript turn for both Agent surfaces (the workbench
// page and the per-page sidebar). The surfaces keep their own CSS worlds, so
// the class names come in via `cx`; everything else — reply gating, tool-chain
// placement, run phases, confirmation cards — has a single source here.

export const runPhase = { Queued: '已排队，等待运行', Running: '正在处理', Cancelling: '正在取消' };
export const visibleQuestion = (question) => question?.split('\n\n[用户附件：')[0];

export function TurnConfirmations({ group, api, filterBundles = (bundles) => bundles, bundleKey = (bundle) => bundle.id }) {
  return (
    <>
      {group.proposals.map((proposal) => (
        <ConfirmCard
          key={proposal.id}
          icon={<SwapOutlined aria-hidden="true" />}
          title={proposal.status === 'Pending' ? '待你确认的业务操作' : '业务操作'}
          meta={<StatusChip status={proposal.status} />}
        >
          <OperationProposal
            proposal={proposal}
            onConfirm={(binding) => api('confirm_operation', binding)}
            onVerify={(binding) => api('verify_operation', binding)}
          />
        </ConfirmCard>
      ))}
      {filterBundles(group.bundles).map((bundle) => (
        <ConfirmCard key={bundleKey(bundle)} icon={<SettingOutlined aria-hidden="true" />} title="应用配置提案">
          <ConfigurationBundle
            bundle={bundle}
            onPrepare={(binding) => api('prepare_configuration_preview', binding)}
            onTransfer={(binding) => api('prepare_configuration_transfer', binding)}
            onPublish={(binding) => api('prepare_configuration_publish', binding)}
            onConfirm={(binding) =>
              api(bundle.preview_available ? 'confirm_configuration' : 'confirm_configuration_publish', binding)
            }
          />
        </ConfirmCard>
      ))}
      {group.confirmations.map((item) => (
        <ConfirmCard
          key={item.id}
          icon={<SafetyCertificateOutlined aria-hidden="true" />}
          title={`${item.status === 'Pending' ? '待你确认的' : ''}${item.purpose === 'publish' ? '配置发布' : '隔离预览'}`}
          meta={<StatusChip status={item.status} />}
        >
          <ConfigurationProposal
            proposal={item}
            onConfirm={(binding) =>
              api(item.purpose === 'publish' ? 'confirm_configuration_publish' : 'confirm_configuration', binding)
            }
            onVerify={(binding) => api('verify_configuration', binding)}
          />
        </ConfirmCard>
      ))}
    </>
  );
}

export function TranscriptTurn({ turn, cx, labelContext, children = null, confirmations = null }) {
  const message = turn.message;
  return (
    <>
      <div className={cx.user}>{visibleQuestion(message.question)}</div>
      <div className={cx.meta}>
        <LinkOutlined aria-hidden="true" />
        <code>{labelContext(message.context)}</code>
      </div>
      {children}
      {/* The reply area appears as soon as there is something real to show —
          an answer, a finished run, or reads the server already authorized
          mid-run. Hiding recorded reads until the answer lands would forfeit
          the transparency the sources record exists for. */}
      {(message.answer || !runPhase[message.status] || turn.tools.length > 0) && (
        <div className={cx.reply}>
          <span className={cx.replyMark}>
            <Spark size={12} />
          </span>
          <div className={cx.answer}>
            {(message.answer || !runPhase[message.status]) && <Prose>{message.answer}</Prose>}
            <ToolChain events={turn.tools} />
          </div>
        </div>
      )}
      {runPhase[message.status] && (
        <div className={cx.thinking}>
          <i />
          <span>{runPhase[message.status]}</span>
        </div>
      )}
      {message.error && (
        <div className={cx.alert} role="alert">
          <WarningFilled aria-hidden="true" />
          <span>{message.error}</span>
        </div>
      )}
      {message.status === 'Cancelled' && <p className={cx.notice}>已取消后续工作；已发生的操作不会自动撤销。</p>}
      {confirmations}
    </>
  );
}
