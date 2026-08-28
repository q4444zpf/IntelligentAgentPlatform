<script setup lang="ts">
defineProps<{
  src: string;
  title: string;
}>();
</script>

<template>
  <iframe
    class="sandboxed-artifact-frame"
    :key="src"
    :src="src"
    sandbox=""
    referrerpolicy="no-referrer"
    :title="title"
  />
</template>

<style scoped>
.sandboxed-artifact-frame {
  display: block;
  width: 100%;
  height: 100%;
  background: #fff;
  border: 0;
}
</style>
