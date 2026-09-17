import { useEffect, useRef } from "react";
import Phaser from "phaser";
import { OfficeScene } from "../office/scene";
import type { Agent } from "../types";

export default function Office({ agents, onSelect }: { agents: Agent[]; onSelect: (name: string) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const game = useRef<Phaser.Game | null>(null);
  const scene = useRef<OfficeScene | null>(null);
  const select = useRef(onSelect);
  select.current = onSelect;

  useEffect(() => {
    if (!host.current || game.current) return;
    const sc = new OfficeScene();
    scene.current = sc;
    game.current = new Phaser.Game({
      type: Phaser.CANVAS,
      parent: host.current,
      width: 648,
      height: 432,
      backgroundColor: "#0b1220",
      pixelArt: true,
      scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
      scene: sc,
      callbacks: { preBoot: () => {} },
    });
    game.current.scene.start("office", { onSelect: (n: string) => select.current(n) });
    return () => { game.current?.destroy(true); game.current = null; scene.current = null; };
  }, []);

  useEffect(() => {
    const sc = scene.current;
    if (!sc) return;
    if (sc.sys && sc.sys.isActive()) sc.sync(agents);
    else setTimeout(() => sc.sync(agents), 300);
  }, [agents]);

  return (
    <div className="relative flex-1 overflow-hidden bg-[#0b1220]">
      <div ref={host} className="h-full w-full" />
      <div className="pointer-events-none absolute bottom-2 left-2 flex gap-2 text-[10px] text-slate-400">
        <span>… trabalhando</span><span className="text-sky-400">⚗ testando</span><span className="text-red-400">! bloqueado</span>
      </div>
    </div>
  );
}
