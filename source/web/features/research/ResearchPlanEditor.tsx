"use client";
import { useEffect, useRef, useState } from "react";
import type { ResearchPlan, ResearchStep } from "@/lib/research-api";

const TOOLS = ["python", "sympy", "z3", "database", "literature", "lean"];
const agents = [["research-planner", "规划"], ["explorer", "探索"], ["proof-builder", "证明探索"], ["experimental-mathematician", "实验与反例"], ["research-skeptic", "质疑"], ["independent-verifier", "复核"], ["research-synthesizer", "综合"]];
const lines = (values: string[] | undefined) => (values || []).join("\n");
const split = (value: string) => value.split("\n").map(item => item.trim()).filter(Boolean);

function GrowingField({label, value, onChange, hint}: {label: string; value: string; onChange: (v: string) => void; hint?: string}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [local, setLocal] = useState<string | null>(null);
  const displayed = local ?? value ?? "";
  useEffect(() => {
    const resize = () => { const el = ref.current; if (el) { el.style.height = "auto"; el.style.height = `${Math.max(44, el.scrollHeight)}px`; } };
    const frame = requestAnimationFrame(resize); window.addEventListener("resize", resize);
    return () => { cancelAnimationFrame(frame); window.removeEventListener("resize", resize); };
  }, [displayed]);
  return <label className="plan-editor-field"><span>{label}</span>{hint ? <small>{hint}</small> : null}<textarea ref={ref} rows={1} value={displayed} onChange={event => { setLocal(event.target.value); onChange(event.target.value); }} onBlur={() => setLocal(null)} /></label>;
}
function ToolChooser({ label, values, onChange }: { label: string; values: string[]; onChange: (v: string[]) => void }) {
  const options = [...TOOLS, ...values.filter(value => !TOOLS.includes(value))];
  return <fieldset className="plan-editor-tools"><legend>{label}</legend><div>{options.map(tool => <label key={tool}><input type="checkbox" checked={values.includes(tool)} onChange={event => onChange(event.target.checked ? [...values, tool] : values.filter(item => item !== tool))} /><span>{tool}</span></label>)}</div></fieldset>;
}
function freshStep(order: number): ResearchStep {
  return { id: `step_${crypto.randomUUID().replaceAll("-", "")}`, title: "新步骤", description: "", status: "pending", order, priority: 3, enabled: true, branch: "main", dependencies: [], assignedAgent: "explorer", requiredTools: [], expectedOutput: "", verificationMethod: "", milestone: "", humanNotes: "", agentNotes: "" };
}
export default function ResearchPlanEditor({ draft, onChange, onCancel, onSave, saving, saveDisabled = false, error }: {
  draft: ResearchPlan; onChange: (plan: ResearchPlan) => void; onCancel: () => void; onSave: () => void; saving: boolean; saveDisabled?: boolean; error: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [expanded, setExpanded] = useState<string>("");
  useEffect(() => { const el = dialog.current; if (el && !el.open) el.showModal(); return () => { if (el?.open) el.close(); }; }, []);
  const patch = (data: Partial<ResearchPlan>) => onChange({ ...draft, ...data });
  const updateStep = (id: string, data: Partial<ResearchStep>) => patch({ steps: draft.steps.map(step => step.id === id ? { ...step, ...data } : step) });
  const sorted = [...draft.steps].sort((a,b) => a.order - b.order);
  const move = (id: string, delta: number) => { const steps = [...sorted]; const pos = steps.findIndex(step => step.id === id); const to = pos + delta; if (pos < 0 || to < 0 || to >= steps.length) return; [steps[pos],steps[to]] = [steps[to],steps[pos]]; patch({steps: steps.map((step,index) => ({...step,order:index}))}); };
  return <dialog ref={dialog} className="plan-editor-dialog" aria-labelledby="plan-editor-title" onCancel={event => { event.preventDefault(); if (!saving) onCancel(); }}>
    <div className="plan-editor-header"><div><h2 id="plan-editor-title">修改研究计划</h2><p>修改后将保存新版本、批准并继续研究。</p></div><button type="button" onClick={onCancel} disabled={saving} aria-label="关闭编辑">×</button></div>
    <fieldset className="plan-editor-body" disabled={saving}>
      <GrowingField label="研究目标" value={draft.objective} onChange={value => patch({objective:value})} />
      <GrowingField label="问题形式化" value={draft.problemFormalization} onChange={value => patch({problemFormalization:value})} />
      <GrowingField label="假设" hint="每行一条假设" value={lines(draft.assumptions)} onChange={value => patch({assumptions:split(value)})} />
      <GrowingField label="定义" hint="每行一条定义" value={lines(draft.definitions)} onChange={value => patch({definitions:split(value)})} />
      <GrowingField label="关键问题" hint="每行一个问题" value={lines(draft.keyQuestions)} onChange={value => patch({keyQuestions:split(value)})} />
      <GrowingField label="拟证引理" hint="每行一条引理" value={lines(draft.proposedLemmas)} onChange={value => patch({proposedLemmas:split(value)})} />
      <GrowingField label="证明策略" value={draft.proofStrategy} onChange={value => patch({proofStrategy:value})} />
      <GrowingField label="反例策略" value={draft.counterexampleStrategy} onChange={value => patch({counterexampleStrategy:value})} />
      <GrowingField label="建议实验" hint="每行一个实验" value={lines(draft.suggestedExperiments)} onChange={value => patch({suggestedExperiments:split(value)})} />
      <GrowingField label="验证策略" value={draft.verificationStrategy} onChange={value => patch({verificationStrategy:value})} />
      <ToolChooser label="所需工具" values={draft.toolsRequired || []} onChange={value => patch({toolsRequired:value})} />
      <GrowingField label="预期产出" hint="每行一项" value={lines(draft.expectedOutputs)} onChange={value => patch({expectedOutputs:split(value)})} />
      <GrowingField label="风险与不确定性" value={draft.riskUncertainty} onChange={value => patch({riskUncertainty:value})} />
      <GrowingField label="研究分支概览" hint="每行一条" value={lines(draft.researchBranches)} onChange={value => patch({researchBranches:split(value)})} />
      <details className="plan-editor-advanced"><summary>管理研究分支</summary>
        {(draft.branches || []).map(branch => <div key={branch.id} className="plan-editor-branch">
          <GrowingField label="分支名称" value={branch.title} onChange={value => patch({branches:draft.branches.map(item => item.id===branch.id ? {...item,title:value} : item)})} />
          <label><input type="checkbox" checked={branch.status === "open"} onChange={event => patch({branches:draft.branches.map(item => item.id===branch.id ? {...item,status:event.target.checked ? "open" : "closed"} : item)})} /> 启用此分支</label>
          <button type="button" onClick={() => patch({branches:draft.branches.filter(item => item.id!==branch.id)})}>删除分支</button>
        </div>)}
        <button type="button" onClick={() => patch({branches:[...(draft.branches || []),{id:`branch_${crypto.randomUUID().replaceAll("-", "")}`,title:"新研究分支",status:"open",priority:3}]})}>添加分支</button>
      </details>
      <div className="plan-editor-steps-header"><h3>研究步骤</h3><button type="button" onClick={() => {const step=freshStep(sorted.length); patch({steps:[...sorted,step]}); setExpanded(step.id);}}>添加步骤</button></div>
      {sorted.map((step,index) => <details className="plan-editor-step" key={step.id} open={expanded === step.id} onToggle={event => {if (event.currentTarget.open) setExpanded(step.id); else if (expanded === step.id) setExpanded("");}}>
        <summary>步骤 {index+1} · {step.title || "未命名"}</summary>
        <div className="plan-editor-step-fields">
          <GrowingField label="步骤标题" value={step.title} onChange={value => updateStep(step.id,{title:value})} />
          <GrowingField label="步骤说明" value={step.description} onChange={value => updateStep(step.id,{description:value})} />
          <ToolChooser label="步骤所需工具" values={step.requiredTools || []} onChange={value => updateStep(step.id,{requiredTools:value})} />
          <GrowingField label="预期产出" value={step.expectedOutput} onChange={value => updateStep(step.id,{expectedOutput:value})} />
          <GrowingField label="验证要求" value={step.verificationMethod} onChange={value => updateStep(step.id,{verificationMethod:value})} />
          <GrowingField label="研究者备注" value={step.humanNotes} onChange={value => updateStep(step.id,{humanNotes:value})} />
          <details className="plan-editor-advanced"><summary>步骤高级设定</summary>
            <label className="plan-editor-field"><span>研究角色</span><select value={step.assignedAgent} onChange={event => updateStep(step.id,{assignedAgent:event.target.value})}>{agents.map(([id,label]) => <option key={id} value={id}>{label}</option>)}{!agents.some(([id]) => id === step.assignedAgent) ? <option value={step.assignedAgent}>{step.assignedAgent}</option> : null}</select></label>
            <label className="plan-editor-field"><span>优先级</span><select value={step.priority} onChange={event => updateStep(step.id,{priority:Number(event.target.value)})}>{[1,2,3,4,5].map(value=><option key={value} value={value}>{value}</option>)}</select></label>
            <GrowingField label="研究分支" value={step.branch} onChange={value => updateStep(step.id,{branch:value})} />
            <GrowingField label="阶段目标" value={step.milestone} onChange={value => updateStep(step.id,{milestone:value})} />
            <GrowingField label="依赖步骤" hint="步骤编号以逗号分隔" value={step.dependencies.join(", ")} onChange={value => updateStep(step.id,{dependencies:value.split(",").map(v=>v.trim()).filter(Boolean)})} />
          </details>
          <div className="plan-editor-step-actions"><button type="button" disabled={index===0} onClick={()=>move(step.id,-1)}>上移</button><button type="button" disabled={index===sorted.length-1} onClick={()=>move(step.id,1)}>下移</button><button type="button" onClick={()=>updateStep(step.id,{enabled:!step.enabled,status:step.enabled?"cancelled":"pending"})}>{step.enabled?"停用":"启用"}</button><button type="button" disabled={sorted.length<=1} onClick={()=>patch({steps:sorted.filter(item=>item.id!==step.id).map((item,order)=>({...item,order}))})}>删除步骤</button></div>
        </div>
      </details>)}
    </fieldset>
    {error ? <p className="plan-editor-error" role="alert">{error}</p> : null}
    <div className="plan-editor-footer"><button type="button" disabled={saving} onClick={onCancel}>取消</button><button type="button" className="plan-editor-save" disabled={saving || saveDisabled || !draft.objective.trim()} onClick={onSave}>{saving ? "正在保存并启动…" : "保存并按新计划继续"}</button></div>
  </dialog>;
}
