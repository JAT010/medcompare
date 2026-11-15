import { useEffect, useState } from "react";
import { DEMO_MODE_EVENT, isDemoMode } from "./demo";

export function useDemoMode(): boolean {
  const [demo, setDemo] = useState<boolean>(() => isDemoMode());

  useEffect(() => {
    function sync(): void {
      setDemo(isDemoMode());
    }
    window.addEventListener(DEMO_MODE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(DEMO_MODE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  return demo;
}
