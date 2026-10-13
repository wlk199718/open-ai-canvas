export {};

declare global {
  interface Window {
    zaowuProvider?: {
      restoreSession(): Promise<{ activated: boolean; token: string; session_expires_at: string } | null>;
      setAutomaticLogin(enabled: boolean): Promise<boolean>;
      getFileStorage(): Promise<{ root: string; activeResults: string; pending: boolean }>;
      openResultDirectory(): Promise<boolean>;
      revealResult(taskId: string, token: string): Promise<boolean>;
      saveResultAs(taskId: string, token: string): Promise<{ saved: boolean }>;
    };
  }
}
