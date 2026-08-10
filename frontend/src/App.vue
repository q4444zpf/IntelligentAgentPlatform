<template>
  <RouterView />
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { usePermissionStore } from '@/stores/permission';

const router = useRouter();
const route = useRoute();
const permissionStore = usePermissionStore();

const SESSION_REFRESH_INTERVAL_MS = 60_000;
const SESSION_ACTIVITY_TIMEOUT_MS = 5 * 60_000;
const activityEvents = ['pointerdown', 'keydown', 'touchstart'] as const;
let lastActivityAt = Date.now();
let refreshTimer: number | undefined;
let redirected = false;

function handleSessionInvalid() {
  if (route.path === '/login' || redirected) return;
  redirected = true;
  router.replace({ path: '/login', query: { redirect: route.fullPath } });
}

function recordActivity() {
  lastActivityAt = Date.now();
}

async function refreshActiveSession() {
  if (
    document.visibilityState !== 'visible' ||
    !permissionStore.isAuthenticated ||
    Date.now() - lastActivityAt >= SESSION_ACTIVITY_TIMEOUT_MS
  ) return;

  try {
    await permissionStore.refreshSession();
  } catch {
    // The request layer dispatches the session-invalid event for authentication failures.
  }
}

onMounted(() => {
  window.addEventListener('iap:session-invalid', handleSessionInvalid);
  activityEvents.forEach((eventName) => window.addEventListener(eventName, recordActivity));
  refreshTimer = window.setInterval(() => void refreshActiveSession(), SESSION_REFRESH_INTERVAL_MS);
});

onBeforeUnmount(() => {
  window.removeEventListener('iap:session-invalid', handleSessionInvalid);
  activityEvents.forEach((eventName) => window.removeEventListener(eventName, recordActivity));
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer);
});
</script>
