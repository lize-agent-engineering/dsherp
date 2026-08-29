import React, { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Drawer,
  Empty,
  Input,
  Layout,
  List,
  Select,
  Spin,
  Tabs,
  Tag,
} from "antd";
import ReactMarkdown from "react-markdown";
import OperationProposal from "./OperationProposal.jsx";
import ConfigurationProposal from "./ConfigurationProposal.jsx";
import ConfigurationBundle from "./ConfigurationBundle.jsx";
import "./AgentWorkbench.css";

const { Sider, Content } = Layout;
const contextLabel = (context) =>
  context?.page_type === "unknown"
    ? "未绑定业务页面"
    : [context?.doctype, context?.name].filter(Boolean).join(" / ");
const visibleQuestion = (question) => question?.split("\n\n[用户附件：")[0];
const viewMethods = {
  pending: "list_pending",
  executions: "list_execution_records",
  configuration: "list_configuration_records",
};

export default function AgentWorkbench({
  api,
  initialSession = null,
  handoff = null,
  pollInterval = 5000,
}) {
  const [view, setView] = useState("chat");
  const [sessions, setSessions] = useState([]);
  const [session, setSession] = useState(null);
  const [selected, setSelected] = useState(initialSession);
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [question, setQuestion] = useState("");
  const [domain, setDomain] = useState("query");
  const [records, setRecords] = useState([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [mobileSessions, setMobileSessions] = useState(false);
  const [mobileContext, setMobileContext] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [title, setTitle] = useState("");

  async function loadSessions(
    nextPage = page,
    nextArchived = archived,
    nextQuery = query,
  ) {
    const result = await api("search_sessions", {
      query: nextQuery,
      page: nextPage,
      archived: nextArchived ? 1 : 0,
    });
    setSessions(result.items);
    setHasMore(result.has_more);
    const target = selected ?? result.items[0]?.id ?? null;
    if (target) {
      setSelected(target);
      setSession(await api("get_session", { session_id: target }));
    } else setSession(null);
  }
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      setBusy(true);
      setError("");
      loadSessions(1, archived, query)
        .catch((e) => live && setError(e.message))
        .finally(() => live && setBusy(false));
    }, 200);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [query, archived]);
  useEffect(() => {
    if (!initialSession) return;
    setSelected(initialSession);
    api("get_session", { session_id: initialSession })
      .then(setSession)
      .catch((e) => setError(e.message));
  }, [initialSession]);
  useEffect(() => {
    if (view === "chat") return;
    setBusy(true);
    api(viewMethods[view], { page: 1 })
      .then((result) => setRecords(result.items))
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  }, [view]);
  useEffect(() => {
    if (view !== "chat" || !selected) return;
    let timer;
    const poll = async () => {
      if (document.visibilityState !== "hidden") {
        try {
          setSession(await api("get_session", { session_id: selected }));
        } catch (e) {
          setError(e.message);
        }
      }
      timer = setTimeout(poll, pollInterval);
    };
    timer = setTimeout(poll, pollInterval);
    return () => clearTimeout(timer);
  }, [view, selected, api, pollInterval]);

  async function choose(id) {
    setBusy(true);
    setError("");
    try {
      setSelected(id);
      setSession(await api("get_session", { session_id: id }));
      setMobileSessions(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function rename() {
    setBusy(true);
    try {
      await api("rename_session", {
        session_id: session.id,
        title: title.trim(),
      });
      setEditingTitle(false);
      await loadSessions();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function toggleArchive() {
    setBusy(true);
    try {
      await api(session.archived ? "restore_session" : "archive_session", {
        session_id: session.id,
      });
      setSession(null);
      setSelected(null);
      await loadSessions(1, session.archived, query);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function fresh() {
    setSelected(null);
    setSession(null);
    setQuestion("");
    setEditingTitle(false);
    setError("");
  }
  async function send() {
    if (!question.trim() || session?.archived) return;
    setBusy(true);
    setError("");
    try {
      const result = await api("send_message", {
        session_id: selected,
        question: question.trim(),
        context: handoff ?? {
          schema_version: 1,
          route: ["dsherp-agent"],
          page_type: "unknown",
          reason: "Agent 工作台未绑定业务页面",
        },
        request_id: crypto.randomUUID(),
        domain,
      });
      setSelected(result.id);
      setSession(result);
      setQuestion("");
      await loadSessions(1, false, query);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    if (!session?.active_run) return;
    setBusy(true);
    setError("");
    try {
      setSession(
        await api("cancel_run", {
          session_id: session.id,
          run_id: session.active_run,
          request_id: crypto.randomUUID(),
        }),
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const grouped = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    return [
      {
        title: "今天",
        items: sessions.filter((item) => item.modified?.slice(0, 10) === today),
      },
      {
        title: "更早",
        items: sessions.filter((item) => item.modified?.slice(0, 10) !== today),
      },
    ].filter((group) => group.items.length);
  }, [sessions]);
  const contextPanel = (
    <aside className="dsh-workbench-context">
      <small>当前上下文</small>
      <strong>{contextLabel(handoff) || "未绑定业务页面"}</strong>
      <p>发送时仍由服务端复核身份、权限、对象版本与已保存事实。</p>
      {handoff?.dirty && <Tag color="gold">包含未保存状态提示</Tag>}
    </aside>
  );
  const sessionPanel = (
    <aside className="dsh-workbench-sessions">
      <Input.Search
        aria-label="搜索会话"
        allowClear
        placeholder="搜索会话"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <div className="dsh-workbench-session-filter">
        <Button
          type={!archived ? "primary" : "text"}
          onClick={() => setArchived(false)}
        >
          进行中
        </Button>
        <Button
          aria-label="归档会话"
          type={archived ? "primary" : "text"}
          onClick={() => setArchived(true)}
        >
          已归档
        </Button>
      </div>
      {session && (
        <div className="dsh-workbench-session-actions">
          {editingTitle ? (
            <>
              <Input
                aria-label="会话标题"
                value={title}
                maxLength={100}
                onChange={(event) => setTitle(event.target.value)}
              />
              <Button
                aria-label="保存会话标题"
                disabled={!title.trim()}
                onClick={rename}
              >
                保存
              </Button>
            </>
          ) : (
            <Button
              aria-label="重命名当前会话"
              onClick={() => {
                setTitle(session.title);
                setEditingTitle(true);
              }}
            >
              重命名
            </Button>
          )}
          <Button
            aria-label={session.archived ? "恢复当前会话" : "归档当前会话"}
            onClick={toggleArchive}
          >
            {session.archived ? "恢复" : "归档"}
          </Button>
        </div>
      )}
      {grouped.length
        ? grouped.map((group) => (
            <section key={group.title}>
              <small>{group.title}</small>
              {group.items.map((item) => (
                <Button
                  block
                  type={item.id === selected ? "primary" : "text"}
                  key={item.id}
                  aria-label={item.title}
                  onClick={() => choose(item.id)}
                >
                  {item.title}
                </Button>
              ))}
            </section>
          ))
        : !busy && (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无会话"
            />
          )}
      <div className="dsh-workbench-pagination">
        <Button
          disabled={page === 1}
          onClick={() => {
            const next = page - 1;
            setPage(next);
            loadSessions(next);
          }}
        >
          上一页
        </Button>
        <span>第 {page} 页</span>
        <Button
          disabled={!hasMore}
          onClick={() => {
            const next = page + 1;
            setPage(next);
            loadSessions(next);
          }}
        >
          下一页
        </Button>
      </div>
    </aside>
  );

  const chat = (
    <div className="dsh-workbench-chat">
      {session?.archived && (
        <Alert type="info" showIcon message="归档会话为只读" />
      )}
      <div role="log" aria-label="对话记录" className="dsh-workbench-timeline">
        {session?.messages?.map((message) => (
          <article key={message.id}>
            <div className="dsh-workbench-user">
              {visibleQuestion(message.question)}
            </div>
            <div className="dsh-workbench-agent">
              <ReactMarkdown skipHtml>{message.answer || ""}</ReactMarkdown>
            </div>
            {message.error && <Alert type="error" message={message.error} />}
          </article>
        ))}
        {session?.proposals?.map((proposal) => (
          <OperationProposal
            key={proposal.id}
            proposal={proposal}
            onConfirm={(binding) => api("confirm_operation", binding)}
            onVerify={(binding) => api("verify_operation", binding)}
          />
        ))}
        {session?.configuration_bundles?.map((bundle) => (
          <ConfigurationBundle
            key={bundle.id}
            bundle={bundle}
            onPrepare={(binding) =>
              api("prepare_configuration_preview", binding)
            }
            onTransfer={(binding) =>
              api("prepare_configuration_transfer", binding)
            }
            onPublish={(binding) =>
              api("prepare_configuration_publish", binding)
            }
            onConfirm={(binding) =>
              api(
                bundle.preview_available
                  ? "confirm_configuration"
                  : "confirm_configuration_publish",
                binding,
              )
            }
          />
        ))}
        {session?.configuration_confirmations?.map((item) => (
          <ConfigurationProposal
            key={item.id}
            proposal={item}
            onConfirm={(binding) =>
              api(
                item.purpose === "publish"
                  ? "confirm_configuration_publish"
                  : "confirm_configuration",
                binding,
              )
            }
            onVerify={(binding) => api("verify_configuration", binding)}
          />
        ))}
        {!session && !busy && <Empty description="选择会话或开始新的对话" />}
      </div>
      {!session?.archived && (
        <form
          className="dsh-workbench-composer"
          onSubmit={(event) => {
            event.preventDefault();
            send();
          }}
        >
          <Input.TextArea
            aria-label="业务问题"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="描述要完成的业务工作…"
            autoSize={{ minRows: 2, maxRows: 6 }}
          />
          <div>
            <Select
              aria-label="任务领域"
              value={domain}
              onChange={setDomain}
              options={[
                { value: "query", label: "只读查询" },
                { value: "operation", label: "业务操作" },
                { value: "configuration", label: "应用配置" },
              ]}
            />
            {session?.active_run ? (
              <Button
                aria-label="停止运行"
                danger
                onClick={cancel}
                loading={busy}
              >
                停止
              </Button>
            ) : (
              <Button
                aria-label="发送"
                type="primary"
                htmlType="submit"
                disabled={!question.trim()}
                loading={busy}
              >
                发送
              </Button>
            )}
          </div>
        </form>
      )}
    </div>
  );
  const recordView = (
    <div className="dsh-workbench-records">
      {records.length ? (
        <List
          dataSource={records}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button
                  key="detail"
                  type="link"
                  aria-label={`查看${item.title || item.action || item.id}详情`}
                  onClick={async () => {
                    await choose(item.session_id);
                    setView("chat");
                  }}
                >
                  查看详情
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={item.title || item.action || item.id}
                description={item.summary || item.status}
              />
              <Tag>{item.status}</Tag>
            </List.Item>
          )}
        />
      ) : (
        !busy && <Empty description="暂无记录" />
      )}
    </div>
  );
  return (
    <Layout className="dsh-workbench">
      <header>
        <div>
          <span>✦</span>
          <strong>Agent 工作台</strong>
        </div>
        <div className="dsh-workbench-header-actions">
          <Button aria-label="新建会话" onClick={fresh}>
            ＋ 新建
          </Button>
          <div className="dsh-workbench-mobile-actions">
            <Button onClick={() => setMobileSessions(true)}>会话</Button>
            <Button onClick={() => setMobileContext(true)}>上下文</Button>
          </div>
        </div>
      </header>
      <Tabs
        activeKey={view}
        onChange={setView}
        items={[
          { key: "chat", label: "对话" },
          { key: "pending", label: "待确认" },
          { key: "executions", label: "执行记录" },
          { key: "configuration", label: "应用配置" },
        ]}
      />
      {error && (
        <Alert
          closable
          onClose={() => setError("")}
          type="error"
          message={error}
        />
      )}
      <Layout>
        <Sider
          width={280}
          theme="light"
          className="dsh-workbench-desktop-sessions"
        >
          {sessionPanel}
        </Sider>
        <Content>
          {busy && <Spin />}
          {view === "chat" ? chat : recordView}
        </Content>
        {view === "chat" && (
          <Sider
            width={260}
            theme="light"
            className="dsh-workbench-desktop-context"
          >
            {contextPanel}
          </Sider>
        )}
      </Layout>
      <Drawer
        placement="left"
        title="会话"
        open={mobileSessions}
        onClose={() => setMobileSessions(false)}
      >
        {sessionPanel}
      </Drawer>
      <Drawer
        placement="right"
        title="上下文"
        open={mobileContext}
        onClose={() => setMobileContext(false)}
      >
        {contextPanel}
      </Drawer>
    </Layout>
  );
}
