<template>
  <RouterView />
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';

const router = useRouter();
const route = useRoute();

function handleSessionInvalid() {
  if (route.path === '/login') return;
  router.replace({ path: '/login', query: { redirect: route.fullPath } });
}

onMounted(() => window.addEventListener('iap:session-invalid', handleSessionInvalid));
onBeforeUnmount(() => window.removeEventListener('iap:session-invalid', handleSessionInvalid));
</script>
