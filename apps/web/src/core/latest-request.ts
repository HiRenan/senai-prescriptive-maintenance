/**
 * Apply only the newest request completion. Earlier work is not claimed as
 * cancelled: it may still finish, but its output and finalizer are ignored.
 */
export function createLatestRequestController<TContext, TOutput>(callbacks: {
  onStart: (context: TContext) => void;
  onApply: (output: TOutput, context: TContext) => void;
  onFinish: (context: TContext) => void;
}): {
  run: (
    task: () => Promise<TOutput>,
    context: TContext,
  ) => Promise<{ applied: boolean }>;
} {
  let generation = 0;

  return {
    async run(task, context) {
      generation += 1;
      const current = generation;
      callbacks.onStart(context);
      try {
        const output = await task();
        if (current !== generation) {
          return { applied: false };
        }
        callbacks.onApply(output, context);
        return { applied: true };
      } finally {
        if (current === generation) {
          callbacks.onFinish(context);
        }
      }
    },
  };
}
