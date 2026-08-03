/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DEV_UNIT_ID?: string;
  readonly VITE_DEV_USER_ID?: string;
  readonly VITE_DEV_PROJECT_ID?: string;
  readonly VITE_DEV_USER_ROLES?: string;
  readonly VITE_DEV_USER_ROLE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
