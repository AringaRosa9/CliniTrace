import createClient from "openapi-fetch";
import type { paths } from "./schema";

export function createApiClient(baseUrl = "") {
  return createClient<paths>({ baseUrl, cache: "no-store" });
}
