<template>
  <section class="page-head">
    <div>
      <h1>{{ title }}</h1>
      <p>{{ subtitle }}</p>
    </div>
    <div class="page-head-actions"><slot /></div>
  </section>
</template>
<script setup lang="ts">
defineProps<{title:string; subtitle:string}>()
</script>
