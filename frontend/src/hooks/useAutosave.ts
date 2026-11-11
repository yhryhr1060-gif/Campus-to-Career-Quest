import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Autosave, browserStorage } from "../state/autosave";

type Options<T> = {
  key: string;
  initial: T;
  enabled: boolean;
  isValue: (value: unknown) => value is T;
  valid: (value: T) => boolean;
  save: (value: T) => Promise<unknown>;
};

export function useAutosave<T>(options: Options<T>) {
  const [controller] = useState(() => new Autosave({ ...options, restore: options.enabled, storage: browserStorage() }));
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const mounted = useRef(false);
  const allowed = useRef(options.enabled);
  allowed.current = options.enabled;
  useEffect(() => {
    mounted.current = true;
    controller.setEnabled(options.enabled);
    return () => { mounted.current = false; controller.setEnabled(false); };
  }, [controller, options.enabled]);
  return {
    ...snapshot,
    update: (value: T) => controller.update(value),
    flush: () => controller.flush(),
    pause: () => controller.setEnabled(false),
    resume: () => { if (mounted.current && allowed.current) controller.setEnabled(true); },
    discardCache: () => controller.discardCache(),
    reset: (value: T) => controller.reset(value),
  };
}
