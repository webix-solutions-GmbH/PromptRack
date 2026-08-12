import { describe, expect, it } from 'vitest';
import { acquireRunLock, isRunExecuting } from '@/lib/run-lock';

describe('run lock', () => {
  it('lets exactly one holder claim a run', async () => {
    const first = await acquireRunLock(1);
    expect(first).not.toBeNull();

    expect(await acquireRunLock(1)).toBeNull();
    expect(await isRunExecuting(1)).toBe(true);

    await first!.release();

    expect(await isRunExecuting(1)).toBe(false);

    const second = await acquireRunLock(1);
    expect(second).not.toBeNull();
    await second!.release();
  });

  it('locks runs independently', async () => {
    const one = await acquireRunLock(1);
    const two = await acquireRunLock(2);

    expect(one).not.toBeNull();
    expect(two).not.toBeNull();
    expect(await isRunExecuting(1)).toBe(true);
    expect(await isRunExecuting(2)).toBe(true);

    await one!.release();
    expect(await isRunExecuting(1)).toBe(false);
    expect(await isRunExecuting(2)).toBe(true);

    await two!.release();
    expect(await isRunExecuting(2)).toBe(false);
  });

  it('is safe to release twice', async () => {
    const lock = await acquireRunLock(3);
    await lock!.release();
    await lock!.release();

    expect(await isRunExecuting(3)).toBe(false);
  });
});
