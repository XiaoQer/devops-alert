export class ApiError extends Error {
  constructor(code, userMessage, status = 0) {
    super(userMessage);
    this.name = "ApiError";
    this.code = code;
    this.userMessage = userMessage;
    this.status = status;
  }
}

export async function requestJson(path, options = {}, messages = {}) {
  try {
    const response = await fetch(path, {
      ...options,
      headers: { Accept: "application/json", ...(options.headers ?? {}) },
    });
    const isJson = response.headers.get("Content-Type")?.includes("application/json");
    const body = isJson ? await response.json() : null;
    if (!response.ok) {
      const hasSafeError = typeof body?.code === "string";
      if (hasSafeError) {
        throw new ApiError(
          body.code,
          typeof body?.message === "string" ? body.message : (messages.error ?? "数据暂时不可用，请稍后重试"),
          response.status,
        );
      }
      if (response.status >= 500) {
        throw new ApiError(
          messages.unavailableCode ?? "api_unavailable",
          messages.unavailable ?? "服务暂时不可用，请稍后重试",
          response.status,
        );
      }
      throw new ApiError(
        messages.errorCode ?? "api_error",
        messages.error ?? "数据暂时不可用，请稍后重试",
        response.status,
      );
    }
    return body;
  } catch (error) {
    if (error?.name === "AbortError" || error instanceof ApiError) throw error;
    throw new ApiError(
      messages.unavailableCode ?? "api_unavailable",
      messages.unavailable ?? "服务暂时不可用，请稍后重试",
    );
  }
}
