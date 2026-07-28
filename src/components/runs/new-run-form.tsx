'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { createRun } from '@/actions/runs';
import { apiPath } from '@/lib/base-path';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

const CUSTOM = '__custom__';

export interface MachineOption {
  id: number;
  name: string;
  baseUrl: string;
}

export interface ModelOption {
  machineId: number;
  modelId: string;
  currentlyLoaded: boolean;
}

export interface GroupOption {
  id: number;
  name: string;
  promptCount: number;
}

type ProbeState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ok'; models: string[] }
  | { status: 'error'; message: string };

export function NewRunForm({
  machines,
  models,
  groups,
}: {
  machines: MachineOption[];
  models: ModelOption[];
  groups: GroupOption[];
}) {
  const router = useRouter();
  const [machineId, setMachineId] = useState(machines[0] ? String(machines[0].id) : '');
  const [modelChoice, setModelChoice] = useState('');
  const [customModel, setCustomModel] = useState('');
  const [selectedGroups, setSelectedGroups] = useState<number[]>([]);
  const [probe, setProbe] = useState<ProbeState>({ status: 'idle' });

  /**
   * Which probe is current. A slow endpoint must not overwrite the result for a
   * machine the user has since switched away from.
   */
  const probeSeq = useRef(0);

  /**
   * Asks the endpoint what it is serving right now.
   *
   * Reuses the machine discovery route rather than a read-only variant on
   * purpose: it upserts `machine_models` and flips `currently_loaded`, which is
   * what makes the "Currently loaded" group below actually current — and it
   * keeps the machine's model history up to date as a side effect. The
   * subsequent `router.refresh()` re-runs the page query so the grouping
   * reflects the new flags; client form state survives a refresh.
   */
  const detectModels = useCallback(
    async (id: string) => {
      if (id === '') return;

      const seq = probeSeq.current + 1;
      probeSeq.current = seq;
      setProbe({ status: 'loading' });

      try {
        const response = await fetch(apiPath(`/api/machines/${id}/discover`), {
          method: 'POST',
        });
        const data = (await response.json()) as
          | { ok: true; discovered: number; models: string[] }
          | { ok: false; error: string };

        if (probeSeq.current !== seq) return;

        if (!data.ok) {
          setProbe({ status: 'error', message: data.error });
          return;
        }

        setProbe({ status: 'ok', models: data.models });
        // Exactly one served model is the common single-model-per-endpoint case
        // (vLLM), so pick it. With several, guessing would be worse than asking.
        if (data.models.length === 1) {
          setModelChoice(data.models[0]);
        }
        router.refresh();
      } catch {
        if (probeSeq.current !== seq) return;
        setProbe({ status: 'error', message: 'Could not reach the endpoint.' });
      }
    },
    [router],
  );

  useEffect(() => {
    // Querying the endpoint is exactly the external-system synchronization an
    // effect is for; the `loading` state it sets first is the visible part of
    // that request, and the sequence guard above handles the overlap when the
    // machine changes mid-flight.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void detectModels(machineId);
  }, [machineId, detectModels]);

  const machineModels = useMemo(
    () => models.filter((model) => String(model.machineId) === machineId),
    [models, machineId],
  );

  // Until the probe answers, fall back to the flags the page was rendered with.
  const detected = probe.status === 'ok' ? new Set(probe.models) : null;
  const isLoaded = (model: ModelOption) =>
    detected ? detected.has(model.modelId) : model.currentlyLoaded;

  const loaded = machineModels.filter(isLoaded);
  const previouslySeen = machineModels.filter((model) => !isLoaded(model));

  const modelId = modelChoice === CUSTOM ? customModel.trim() : modelChoice;
  const canSubmit =
    machineId !== '' && modelId.length > 0 && selectedGroups.length > 0;

  function handleMachineChange(value: string) {
    setMachineId(value);
    setModelChoice('');
  }

  function toggleGroup(id: number) {
    setSelectedGroups((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );
  }

  if (machines.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-200 px-6 py-12 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
        Add a machine first — a run needs an endpoint to talk to.
      </div>
    );
  }

  return (
    <form
      action={createRun}
      className="flex max-w-2xl flex-col gap-6 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800"
    >
      <input type="hidden" name="modelId" value={modelId} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="machineId">
            Machine *
          </label>
          <select
            id="machineId"
            name="machineId"
            value={machineId}
            onChange={(event) => handleMachineChange(event.target.value)}
            className={inputClass}
          >
            {machines.map((machine) => (
              <option key={machine.id} value={machine.id}>
                {machine.name}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <div className="flex items-baseline justify-between gap-2">
            <label className={labelClass} htmlFor="modelChoice">
              Model *
            </label>
            <button
              type="button"
              disabled={probe.status === 'loading'}
              onClick={() => void detectModels(machineId)}
              className="text-xs font-medium text-zinc-500 underline-offset-2 hover:underline disabled:opacity-50 dark:text-zinc-400"
            >
              {probe.status === 'loading' ? 'Detecting…' : 'Re-detect'}
            </button>
          </div>
          <select
            id="modelChoice"
            value={modelChoice}
            onChange={(event) => setModelChoice(event.target.value)}
            className={inputClass}
          >
            <option value="">Select a model…</option>
            {loaded.length > 0 && (
              <optgroup label="Currently loaded">
                {loaded.map((model) => (
                  <option key={model.modelId} value={model.modelId}>
                    {model.modelId}
                  </option>
                ))}
              </optgroup>
            )}
            {previouslySeen.length > 0 && (
              <optgroup label="Previously seen">
                {previouslySeen.map((model) => (
                  <option key={model.modelId} value={model.modelId}>
                    {model.modelId}
                  </option>
                ))}
              </optgroup>
            )}
            <option value={CUSTOM}>Other — type a model id…</option>
          </select>

          {probe.status === 'loading' && (
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Asking the endpoint what it is serving…
            </p>
          )}
          {probe.status === 'ok' && (
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              {probe.models.length === 0
                ? 'The endpoint reports no loaded model.'
                : probe.models.length === 1
                  ? `Serving ${probe.models[0]} — selected.`
                  : `${probe.models.length} models loaded — pick one.`}
            </p>
          )}
          {probe.status === 'error' && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              {probe.message} Previously seen models are still selectable.
            </p>
          )}
        </div>
      </div>

      {(modelChoice === CUSTOM || machineModels.length === 0) && (
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="customModel">
            Model id
          </label>
          <input
            id="customModel"
            value={customModel}
            onChange={(event) => {
              setCustomModel(event.target.value);
              setModelChoice(CUSTOM);
            }}
            placeholder="llama-3.1-8b-instruct"
            className={`${inputClass} font-mono`}
          />
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Free text — the model does not have to be discovered yet.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <span className={labelClass}>Prompt groups *</span>
        {groups.length === 0 ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            No prompt groups yet — create one under Prompts.
          </p>
        ) : (
          <div className="flex flex-col gap-2 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
            {groups.map((group) => {
              const empty = group.promptCount === 0;
              return (
                <label
                  key={group.id}
                  className={`flex items-center gap-2 text-sm ${
                    empty
                      ? 'text-zinc-400 dark:text-zinc-600'
                      : 'text-zinc-700 dark:text-zinc-300'
                  }`}
                >
                  <input
                    type="checkbox"
                    name="groupIds"
                    value={group.id}
                    disabled={empty}
                    checked={selectedGroups.includes(group.id)}
                    onChange={() => toggleGroup(group.id)}
                  />
                  <span>{group.name}</span>
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">
                    {group.promptCount} {group.promptCount === 1 ? 'prompt' : 'prompts'}
                  </span>
                </label>
              );
            })}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="temperature">
            Temperature
          </label>
          <input
            id="temperature"
            name="temperature"
            type="number"
            step="0.1"
            min="0"
            max="2"
            placeholder="server default"
            className={inputClass}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="maxTokens">
            Max tokens
          </label>
          <input
            id="maxTokens"
            name="maxTokens"
            type="number"
            step="1"
            min="1"
            placeholder="server default"
            className={inputClass}
          />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label className={labelClass} htmlFor="comment">
          Comment
        </label>
        <textarea
          id="comment"
          name="comment"
          rows={3}
          placeholder="What are you testing with this run?"
          className={inputClass}
        />
      </div>

      <div>
        <button
          type="submit"
          disabled={!canSubmit}
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          Start run
        </button>
      </div>
    </form>
  );
}
