import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('ArtifactListView', () => {
  it('renders artifact metadata and a download action', () => {
    const source = readFileSync(new URL('./ArtifactListView.vue', import.meta.url), 'utf8');

    expect(source).toContain('成果文件');
    expect(source).toContain('下载');
    expect(source).toContain('filename');
    expect(source).toContain('run_id');
  });

  it('downloads through an anchor without opening a preview tab', () => {
    const source = readFileSync(new URL('./ArtifactListView.vue', import.meta.url), 'utf8');

    expect(source).toContain("document.createElement('a')");
    expect(source).toContain('link.download = artifact.filename');
    expect(source).not.toContain('window.open(');
  });
});
