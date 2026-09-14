// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import ControlCommandView from './ControlCommandView.vue';
vi.mock('@/api/control', () => ({ controlApi: { listCommands: vi.fn().mockResolvedValue([]) } }));
describe('ControlCommandView', () => { it('renders the operations title', async () => { const wrapper = mount(ControlCommandView, { global: { plugins: [createPinia()], stubs: { 'a-card': { template: '<div><slot /></div>' }, 'a-table': true, 'a-typography-title': { template: '<h1><slot /></h1>' }, 'a-typography-text': true, 'a-space': { template: '<div><slot /></div>' }, 'a-select': true, 'a-button': true, 'a-alert': true, 'a-tag': true, 'a-empty': true } } }); await Promise.resolve(); expect(wrapper.text()).toContain('调度控制'); }); });
