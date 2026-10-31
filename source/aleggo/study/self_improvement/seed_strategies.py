"""Initial seed strategies for Execution Agents A & B and Meta-Reflector."""
from __future__ import annotations

from aleggo.study.self_improvement.models import Strategy, utc_now_iso

EXECUTION_SEED_TEMPLATES: list[dict[str, object]] = [
    {
        "base_id": "small_scale_numeric_probe",
        "name": "先做小规模数值实验找规律",
        "applicable_conditions": "题目涉及参数 n、递推关系、数列求和、组合计数或一般性代数恒等式，直接符号推导较为复杂时。",
        "execution_steps": [
            "步骤 1：选取小规模参数（如 n = 1, 2, 3, 4, 5）编写 Python 脚本计算精确值。",
            "步骤 2：观察小规模输出序列的差分、比值、质因数分解或多项式拟合规律，提出显式猜想。",
            "步骤 3：用 n = 6, 7, 8 的独立计算检验猜想，再进行符号代数证明或通项化简。",
        ],
        "taboos_and_counterexamples": [
            "禁忌：仅凭前 2 项就草率断定规律（例如 Borwein 积分或 n^2 + n + 41 素数假象）。",
            "禁忌：在数值实验中混用低精度浮点数导致大整数截断误差，应优先使用 Python 精确整数或 Fractions / SymPy Rational。",
        ],
    },
    {
        "base_id": "counterexample_first_check",
        "name": "反例优先与边界情形检验",
        "applicable_conditions": "题目要求判断命题真伪、求极值边界、求解含参方程或不等式成立范围时。",
        "execution_steps": [
            "步骤 1：优先测试退化与边界情形（0、1、-1、质数、奇偶极端情形、奇异矩阵）。",
            "步骤 2：调用 run_python 或 z3_check 在有限网格内系统搜索潜在反例。",
            "步骤 3：若未发现反例，提取边界紧约束条件指导正面推导；若发现反例，立即验证反例满足全部题设条件。",
        ],
        "taboos_and_counterexamples": [
            "禁忌：忽略题设定义域限制（如正整数 vs 非负整数、互素条件），将不满足题设的值误判为反例。",
            "禁忌：未做边界核验就直接展开冗长的正面证明。",
        ],
    },
    {
        "base_id": "stepwise_decomposition",
        "name": "分步拆解与中间引理验证",
        "applicable_conditions": "多阶段复合问题（如先求递推通项再求级数极限、先对角化矩阵再算高次幂迹、数论模运算组合问题）。",
        "execution_steps": [
            "步骤 1：将原问题拆解为 2～3 个可独立验证的子问题（中间引理）。",
            "步骤 2：每完成一个子问题，立即调用 Python / SymPy 对该中间结论做数值或符号校验，确认无误后再代入下一步。",
            "步骤 3：汇总各子问题结果，并对最终合成答案做端到端独立复核。",
        ],
        "taboos_and_counterexamples": [
            "禁忌：连续多步长链条手算推导而不校验中间结果，导致早期符号或系数错误向后传播。",
            "禁忌：子问题接口定义不清（如混用 0-indexed 与 1-indexed 下标）。",
        ],
    },
    {
        "base_id": "simplify_before_compute",
        "name": "先代数/符号化简再代入计算",
        "applicable_conditions": "含复杂代数分式、三角恒等式、对称多项式、行列式或定积分计算题。",
        "execution_steps": [
            "步骤 1：利用对称性、奇偶性、周期性或因式分解先对原式做结构化简。",
            "步骤 2：调用 SymPy（simplify / factor / expand / trigsimp）将表达式化为最简标准型。",
            "步骤 3：在最简表达式上代入具体数值或积分上下限完成计算，并与直接数值积分/求值比对。",
        ],
        "taboos_and_counterexamples": [
            "禁忌：未化简就直接对高次展开式硬算，引发符号膨胀或数值相消误差。",
            "禁忌：化简过程中约去可能为零的因式而未分类讨论。",
        ],
    },
    {
        "base_id": "auxiliary_generating_function",
        "name": "构造辅助函数或生成函数",
        "applicable_conditions": "涉及带权二项式系数求和、复杂递推数列、积分含参求导（Feynman 技巧）或特征多项式根的对称和问题。",
        "execution_steps": [
            "步骤 1：识别目标量所属的参数族，构造对应的普通/指数生成函数 P(x) 或含参辅助函数 F(t)。",
            "步骤 2：利用代数运算、微分方程、留数/系数提取或韦达定理，通过 SymPy 求解辅助函数。",
            "步骤 3：提取目标系数或代入特殊参数值还原原问题答案，并用小规模直接求和校验。",
        ],
        "taboos_and_counterexamples": [
            "禁忌：对发散级数直接做形式微分/积分交换而不核对收敛半径或有限截断等价性。",
            "禁忌：生成函数下标偏移量对齐错误（如 [x^n] 与 [x^{n-1}] 混淆）。",
        ],
    },
]

META_SEED_STRATEGIES: list[dict[str, object]] = [
    {
        "id": "strat_meta_evidence_strict_review",
        "name": "元反思严格证据链与过拟合防范策略",
        "applicable_conditions": "审批反思 Agent 提交的 StrategyProposal 以及审查实践 Agent 产出的 ExperimentReport 时。",
        "execution_steps": [
            "步骤 1：核查申请中的 evidence_trace_ids 是否真实存在，且所指出的失败/低效原因与轨迹中的工具输出和推理步骤一致。",
            "步骤 2：检查新策略的结构化修改是否具有普适数学方法论意义，拒绝针对单道题硬编码答案或过拟合特定数字的修改。",
            "步骤 3：为通过初审的申请指派覆盖相关数学分支的 benchmark 题目子集与运行次数 k，并在实验后结合通过率与 token 增幅做出终审。",
        ],
        "taboos_and_counterexamples": [
            "禁忌：批准没有真实轨迹支撑或仅修改表面措辞而无实质执行步骤改进的提案。",
            "禁忌：元反思 Agent 批准自己提出的元策略修改（必须经实验达标 + 人工确认）。",
        ],
    }
]


def build_seed_strategies_for_agent(agent_id: str) -> list[Strategy]:
    """Build identical initial active strategies for agent_a or agent_b."""
    now = utc_now_iso()
    strategies: list[Strategy] = []
    for tpl in EXECUTION_SEED_TEMPLATES:
        strat_id = f"strat_{agent_id}_{tpl['base_id']}"
        strategies.append(
            Strategy(
                id=strat_id,
                name=str(tpl["name"]),
                version=1,
                owner=agent_id,
                status="active",
                applicable_conditions=str(tpl["applicable_conditions"]),
                execution_steps=list(tpl["execution_steps"]),  # type: ignore[arg-type]
                taboos_and_counterexamples=list(tpl["taboos_and_counterexamples"]),  # type: ignore[arg-type]
                parent_version=None,
                origin_proposal_id=None,
                evidence=[],
                proposed_by="system_seed",
                approved_by="strategy_manager",
                created_at=now,
                updated_at=now,
            )
        )
    return strategies


def build_meta_seed_strategies() -> list[Strategy]:
    now = utc_now_iso()
    strategies: list[Strategy] = []
    for tpl in META_SEED_STRATEGIES:
        strategies.append(
            Strategy(
                id=str(tpl["id"]),
                name=str(tpl["name"]),
                version=1,
                owner="meta",
                status="active",
                applicable_conditions=str(tpl["applicable_conditions"]),
                execution_steps=list(tpl["execution_steps"]),  # type: ignore[arg-type]
                taboos_and_counterexamples=list(tpl["taboos_and_counterexamples"]),  # type: ignore[arg-type]
                parent_version=None,
                origin_proposal_id=None,
                evidence=[],
                proposed_by="system_seed",
                approved_by="strategy_manager",
                created_at=now,
                updated_at=now,
            )
        )
    return strategies
