export interface Integration {
  connected: boolean;
  loading?: boolean;
  /** One line shown under the name when connected. */
  detail: string;
  /** Who the credential belongs to (login or display name), when known. */
  account?: string;
  /** Returns a success message, or {error}. Omit when the integration has no cheap test. */
  test?: () => Promise<string | { error: string }>;
  disconnect: () => Promise<unknown>;
  disconnectMessage: string;
}
