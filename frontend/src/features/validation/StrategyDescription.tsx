import type { ValidationPage } from "../../api/validation";
const factors: Record<string, string> = { quality: "质量", growth: "成长", valuation: "估值", momentum: "动量", industry: "行业", low_volatility: "低波动", liquidity: "流动性" };
export function StrategyDescription({ template }: { template?: ValidationPage["templates"][number] }) {
  return <div className="strategy-description"><p>{template?.description ?? "该实验使用因子综合评分进行选股。"}</p>{template?.weights && <div className="weight-tags">{Object.entries(template.weights).map(([key, weight]) => <span key={key}>{factors[key] ?? key} <strong>{(weight * 100).toFixed(0)}%</strong></span>)}</div>}
    <details><summary>查看具体选股与成交规则</summary><ol><li>使用信号日当时的沪深300成分股和已披露财报，避免使用未来信息。</li><li>按所选模板对可用因子加权；可用权重、综合覆盖率达到 70% 才参与排名，实际可用权重重新归一化。</li><li>过滤不符合历史可交易条件的股票，按综合分从高到低选择前 N 只，形成等权目标。</li><li>每月末收盘生成信号，下一个交易日开盘按持仓差额模拟成交；受停牌、涨跌停等约束。</li><li>成交时扣除费用，每日收盘估值；不足样本或不完整结果不展示完整绩效。</li></ol><p className="muted">因子策略独立运行，不使用模型预测。模板版本：{template?.version ?? "--"}</p></details>
  </div>;
}
