import type {
  WorkflowEvent,
  WorkflowEventParseResult,
  WorkflowError,
  WorkflowState,
} from "./workflow-types.ts";

export interface WorkflowStreamState {
  runId: string | null;
  lastSeq: number;
  currentStage: string | null;
  transient: Record<string, string>;
  dirtyStages: string[];
  dirtyRuns: string[];
  pendingCheckpointStages: string[];
  checkpoint: WorkflowState | null;
  checkpointRequired: boolean;
  recoverableError: WorkflowError | null;
}

const stageKey = (runId: string, stageId: string) => `${runId}:${stageId}`;

export function initialWorkflowStreamState(
  runId: string | null = null,
  lastSeq = 0,
  checkpoint: WorkflowState | null = null,
): WorkflowStreamState {
  return {
    runId, lastSeq, currentStage: checkpoint?.current_stage ?? null, transient: {},
    dirtyStages: [], dirtyRuns: [], pendingCheckpointStages: [], checkpoint,
    checkpointRequired: false, recoverableError: null,
  };
}

export function reduceWorkflowStream(
  state: WorkflowStreamState,
  input: WorkflowEvent | WorkflowEventParseResult,
): WorkflowStreamState {
  if ("kind" in input) {
    if (input.kind === "error") return { ...state, recoverableError: input.error };
    if (input.kind === "ignored") return state;
    return reduceWorkflowStream(state, input.event);
  }

  const event = input;
  const sameRun = state.runId === event.run_id;
  const lastSeq = sameRun ? state.lastSeq : 0;
  if (event.seq <= lastSeq) return state;

  const previousStage = sameRun ? state.currentStage : null;
  const eventStage = "stage_id" in event ? event.stage_id : previousStage;
  const hasGap = event.seq > lastSeq + 1;
  const dirtyStages = sameRun ? [...state.dirtyStages] : [];
  const dirtyRuns = sameRun ? [...state.dirtyRuns] : [];
  const pendingCheckpointStages = sameRun ? [...state.pendingCheckpointStages] : [];
  let transient = sameRun ? { ...state.transient } : {};
  if (hasGap) {
    if (previousStage) {
      const previousKey = stageKey(event.run_id, previousStage);
      if (!dirtyStages.includes(previousKey)) dirtyStages.push(previousKey);
      transient[previousStage] = "";
      if (event.type === "stage.delta" && event.stage_id !== previousStage) {
        const eventKey = stageKey(event.run_id, event.stage_id);
        if (!dirtyStages.includes(eventKey)) dirtyStages.push(eventKey);
      }
    } else if (!dirtyRuns.includes(event.run_id)) {
      dirtyRuns.push(event.run_id);
    }
  }

  const isDirty = dirtyRuns.includes(event.run_id)
    || (eventStage ? dirtyStages.includes(stageKey(event.run_id, eventStage)) : false);
  if (event.type === "stage.delta" && !hasGap && !isDirty) {
    transient[event.stage_id] = (transient[event.stage_id] ?? "") + event.delta;
  }
  if (event.type === "stage.completed" && !pendingCheckpointStages.includes(event.stage_id)) {
    pendingCheckpointStages.push(event.stage_id);
  }

  return {
    ...state,
    runId: event.run_id,
    lastSeq: event.seq,
    currentStage: event.type === "stage.started" || event.type === "stage.delta"
      ? event.stage_id
      : previousStage,
    transient,
    dirtyStages,
    dirtyRuns,
    pendingCheckpointStages,
    checkpoint: sameRun ? state.checkpoint : null,
    checkpointRequired: (sameRun ? state.checkpointRequired : false)
      || hasGap || event.type === "stage.completed",
  };
}

export function applyWorkflowCheckpoint(
  state: WorkflowStreamState,
  checkpoint: WorkflowState,
): WorkflowStreamState {
  if (state.runId !== null && checkpoint.event_run_id !== state.runId) return state;
  if ((checkpoint.event_seq ?? 0) < state.lastSeq) return state;
  const terminalStages = new Set(
    Object.values(checkpoint.stages ?? {})
      .filter((stage) => stage.status !== "running" && stage.status !== "pending")
      .map((stage) => stage.id),
  );
  if (state.pendingCheckpointStages.some((stageId) => !terminalStages.has(stageId))) return state;
  const stageIdOfKey = (key: string) => key.slice(key.lastIndexOf(":") + 1);
  const dirtyStages = state.dirtyStages.filter((key) => !terminalStages.has(stageIdOfKey(key)));
  const currentStage = checkpoint.current_stage ?? null;
  const currentIsTerminal = currentStage === null || terminalStages.has(currentStage);
  const dirtyRuns = currentIsTerminal ? state.dirtyRuns.filter((runId) => runId !== state.runId) : state.dirtyRuns;
  return {
    ...state,
    checkpoint,
    currentStage,
    lastSeq: Math.max(state.lastSeq, checkpoint.event_seq ?? 0),
    transient: Object.fromEntries(Object.entries(state.transient).filter(([id]) => !terminalStages.has(id))),
    dirtyStages,
    dirtyRuns,
    pendingCheckpointStages: state.pendingCheckpointStages.filter((stageId) => !terminalStages.has(stageId)),
    checkpointRequired: dirtyStages.length > 0 || dirtyRuns.includes(state.runId ?? ""),
  };
}
