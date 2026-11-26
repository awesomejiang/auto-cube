import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Mode = "preview" | "test" | "potential" | "either" | "cp";
type Report = { running: boolean; error: boolean; lines: string[] };
const emptyReport: Report = { running: false, error: false, lines: [] };
const targetKey = "quanah-cube.targets";
const categories = "STR DEX INT LUK ALL ATT MATT HP MP DEF BOSS IED DMG CRIT CRITDMG DROP MESO CD".split(" ");
const cpKey = "quanah-cube.stop-on-cp";

function selectedCategories(target: string) {
  return [...new Set(target.split("|").map(part => part.trim().toUpperCase()))]
    .filter(part => categories.includes(part));
}

function loadTargets(): string[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(targetKey) ?? "null");
    if (Array.isArray(saved) && saved.length === 3 && saved.every(value => typeof value === "string")) {
      return saved.map(value => selectedCategories(value).join("|"));
    }
  } catch {
    // Invalid stored preferences fall back to the example.
  }
  return ["STR", "STR|ALL", ""];
}

function App() {
  const [targets, setTargets] = useState(loadTargets);
  const [stopOnCp, setStopOnCp] = useState(() => localStorage.getItem(cpKey) === "true");
  const [report, setReport] = useState(emptyReport);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [mode, setMode] = useState<Mode>("test");
  const log = useRef<HTMLPreElement>(null);
  const busy = pending || report.running;
  const hasTargets = targets.some(Boolean);

  useEffect(() => {
    if (!report.running) return;
    let active = true;
    let timer: number;
    const poll = async () => {
      try {
        const next = await invoke<Report>("get_report");
        if (!active) return;
        setReport(next);
        if (next.running) timer = window.setTimeout(poll, 250);
      } catch (error) {
        if (active) {
          setMessage(String(error));
          timer = window.setTimeout(poll, 1000);
        }
      }
    };
    timer = window.setTimeout(poll, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [report.running]);

  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [report.lines]);

  function updateTarget(index: number, value: string) {
    const next = targets.map((target, position) => position === index ? value : target);
    setTargets(next);
    localStorage.setItem(targetKey, JSON.stringify(next));
  }

  async function start(nextMode: Mode) {
    const normalized = targets.map(target => target.trim().toUpperCase());
    setPending(true);
    setMessage("");
    setMode(nextMode);
    try {
      await invoke("start_run", { mode: nextMode, targets: normalized.map(target => target || "*"), readCp: stopOnCp });
      setReport({ running: true, error: false, lines: [] });
    } catch (error) {
      setMessage(String(error));
    } finally {
      setPending(false);
    }
  }

  function startReroll() {
    if (!hasTargets && !stopOnCp) {
      setMessage("请选择至少一个目标词条，或勾选 CP 增加时停止。");
      return;
    }
    void start(hasTargets ? (stopOnCp ? "either" : "potential") : "cp");
  }

  async function stop() {
    setPending(true);
    try {
      await invoke("stop_run");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setPending(false);
    }
  }

  const readOnly = mode === "test" || mode === "preview";
  const countdown = report.running ? report.lines.at(-1)?.match(/^切回游戏倒计时 (\d)$/)?.[1] : undefined;
  const outputLines = report.lines.filter(line => !line.startsWith("切回游戏倒计时 "));
  const status = report.running ? (readOnly ? "正在读取" : "正在运行") :
    report.error ? "运行异常" : report.lines.length ? "已结束" : "待机";

  return (
    <main>
      <header className="page-header">
        <div className="mark" aria-hidden="true">Q</div>
        <div><h1>Quanah Cube</h1><p>潜能词条与战斗力</p></div>
        <span className="status" data-active={report.running} role="status">{status}</span>
      </header>
        <section className="panel" aria-labelledby="targets-heading">
          <h2 id="targets-heading">目标词条</h2>
          <p>三条可换序。每条可选多个备选，留空表示任意。</p>
          <div className="target-list">
            {targets.map((target, index) => {
              const selected = selectedCategories(target);
              const available = categories.filter(category => !selected.includes(category));
              return <div className="target-row" key={index}>
                <span>条件 {index + 1}</span>
                <div className="target-controls">
                  {selected.map(category => (
                    <button type="button" className="chip" disabled={busy} key={category}
                      aria-label={`移除条件 ${index + 1} 的 ${category}`}
                      onClick={() => updateTarget(index, selected.filter(value => value !== category).join("|"))}>
                      {category} <span aria-hidden="true">×</span>
                    </button>
                  ))}
                  <select aria-label={`给条件 ${index + 1} 添加词条`} value="" disabled={busy || !available.length}
                    onChange={event => {
                      if (event.target.value) updateTarget(index, [...selected, event.target.value].join("|"));
                    }}>
                    <option value="" disabled>{selected.length ? "添加备选" : "任意 · 选择词条"}</option>
                    {available.map(category => <option key={category} value={category}>{category}</option>)}
                  </select>
                </div>
              </div>;
            })}
          </div>
          <label className="cp-toggle">
            <input type="checkbox" checked={stopOnCp} disabled={busy}
              onChange={event => {
                setStopOnCp(event.target.checked);
                localStorage.setItem(cpKey, String(event.target.checked));
              }} />
            <span>CP 增加时停止</span>
          </label>
        </section>
      <section className="panel result" aria-labelledby="result-heading">
        <div className="result-heading"><h2 id="result-heading">读取结果</h2>
          {countdown
            ? <span className="countdown" role="status">切回游戏 · {countdown} 秒</span>
            : <span>先打开魔方结果页，再在 5 秒内切回游戏</span>}
        </div>
        <pre ref={log} tabIndex={0} aria-label="运行输出">{outputLines.join("\n") || "试读或测试后，这里会显示当前读数。"}</pre>
        {message && <p className="error" role="alert">{message}</p>}
      </section>
      <footer>
        <button disabled={busy} onClick={() => void start("preview")}>试读</button>
        <button className="test" disabled={busy} onClick={() => void start("test")}>测试词条 · 只读</button>
        <div className="spacer" />
        {report.running
          ? <button className="stop" disabled={pending} onClick={() => void stop()}>停止</button>
          : <button className="start" disabled={busy} onClick={startReroll}>开始重洗</button>}
      </footer>
    </main>
  );
}

createRoot(document.getElementById("app")!).render(<App />);
