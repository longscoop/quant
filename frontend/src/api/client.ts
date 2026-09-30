const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiRequestError extends Error {
  constructor(message = "服务暂时不可用，请稍后重试。") {
    super(message);
  }
}

export async function getJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, { headers: { Accept: "application/json" } });
  } catch {
    throw new ApiRequestError();
  }
  if (!response.ok) {
    throw new ApiRequestError();
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiRequestError();
  }
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiRequestError();
  }
  if (!response.ok) {
    if (response.status === 422) {
      try {
        const body = await response.json() as { detail?: unknown };
        if (typeof body.detail === "string") throw new ApiRequestError(body.detail);
        if (Array.isArray(body.detail)) {
          const fields: Record<string, string> = { name: "组合名称", initial_capital: "模拟本金", transaction_cost_bps: "费用", flow_date: "生效日期", amount: "金额", note: "备注", start_date: "开始日期", end_date: "结束日期", cost_bps: "交易成本", top_n: "持仓数量", experiment_name: "实验名称" };
          const issues = body.detail.map((issue: { loc?: string[]; type?: string }) => {
            const field = fields[issue.loc?.at(-1) ?? ""] ?? "输入参数";
            return `${field}：${issue.type === "missing" ? "请填写此项" : "格式或取值范围不正确"}`;
          });
          throw new ApiRequestError(issues.join("；"));
        }
      } catch (error) {
        if (error instanceof ApiRequestError) throw error;
      }
    }
    throw new ApiRequestError();
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiRequestError();
  }
}
