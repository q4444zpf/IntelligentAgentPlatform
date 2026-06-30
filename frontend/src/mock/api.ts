import { modelProviders, personalResources, publicResources, sandboxTasks } from './platform';

const wait = (ms = 260) => new Promise((resolve) => window.setTimeout(resolve, ms));

export async function getDashboardData() {
  await wait();
  return {
    metrics: {
      personalResources: personalResources.length,
      publicResources: publicResources.length,
      modelProviders: modelProviders.length,
      sandboxTasks: sandboxTasks.length,
    },
    personalResources,
    publicResources,
    modelProviders,
    sandboxTasks,
  };
}

export async function getPersonalResources() {
  await wait();
  return personalResources;
}

export async function getPublicResources() {
  await wait();
  return publicResources;
}

export async function getModelProviders() {
  await wait();
  return modelProviders;
}

export async function getSandboxTasks() {
  await wait();
  return sandboxTasks;
}
