const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiRequestError extends Error {
  constructor() {
    super("服务暂时不可用，请稍后重试。");
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
