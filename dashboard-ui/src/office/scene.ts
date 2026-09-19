import Phaser from "phaser";
import type { Agent } from "../types";

/**
 * Pixel-art office: four rooms, desks, a sofa, a lab lamp, all rendered procedurally. Each
 * Loompa uses a 32x32 PNG at public/sprites/loompa-<role>.png when present, falling back to a
 * procedurally-drawn 16x24 placeholder otherwise. Its animation reflects its state:
 *   IDLE     -> sitting on the lounge sofa / resting at desk, slow breathing
 *   WORKING  -> at its desk, hands "typing" + floating keystrokes
 *   TESTING  -> in the QA lab, blue lamp blinking
 *   BLOCKED  -> red "!" bouncing over its head
 */

const ROOMS: Record<string, { x: number; y: number; w: number; h: number; label: string; color: number }> = {
  dev: { x: 16, y: 40, w: 300, h: 190, label: "Sala de Dev", color: 0x1e293b },
  meeting: { x: 332, y: 40, w: 300, h: 190, label: "Sala de Reunião", color: 0x1f2937 },
  lounge: { x: 16, y: 246, w: 300, h: 170, label: "Lounge / Café", color: 0x27272a },
  qa: { x: 332, y: 246, w: 300, h: 170, label: "Finanças & QA", color: 0x1a2e3b },
};

const ROLE_COLORS: Record<string, number> = {
  master: 0xf59e0b, product: 0xa78bfa, architect: 0x60a5fa, worker: 0x34d399, inspector: 0x38bdf8,
  deployer: 0xf472b6, finance: 0xfbbf24, kaizen: 0x4ade80, storyteller: 0xfb7185, metrics: 0x94a3b8, compliance: 0xc084fc,
};

interface Sprite {
  root: Phaser.GameObjects.Container;
  body: Phaser.GameObjects.Rectangle | Phaser.GameObjects.Image;
  badge: Phaser.GameObjects.Text;
  label: Phaser.GameObjects.Text;
  badgeBase: number;
  state: string;
  t: number;
  home: { x: number; y: number };
  seat: { x: number; y: number };
}

export class OfficeScene extends Phaser.Scene {
  private sprites = new Map<string, Sprite>();
  private agents: Agent[] = [];
  private onSelect: (name: string) => void = () => {};
  private lamp?: Phaser.GameObjects.Rectangle;
  private seats: Record<string, { x: number; y: number }[]> = { dev: [], meeting: [], lounge: [], qa: [] };

  constructor() { super("office"); }

  init(data: { onSelect: (name: string) => void }) { this.onSelect = data.onSelect; }

  preload() {
    // Drop a 32x32 PNG at dashboard-ui/public/sprites/loompa-<role>.png to replace the
    // procedural placeholder for that role; missing files just fall back silently in spawn().
    Object.keys(ROLE_COLORS).forEach((role) => {
      this.load.image(`loompa-${role}`, `/sprites/loompa-${role}.png`);
    });
  }

  create() {
    const g = this.add.graphics();
    g.fillStyle(0x0b1220, 1).fillRect(0, 0, 648, 432);
    // floor tiles
    for (let y = 0; y < 432; y += 16) for (let x = 0; x < 648; x += 16) {
      g.fillStyle((x / 16 + y / 16) % 2 ? 0x0e172a : 0x101b30, 1).fillRect(x, y, 16, 16);
    }
    Object.entries(ROOMS).forEach(([key, r]) => {
      g.fillStyle(r.color, 1).fillRect(r.x, r.y, r.w, r.h);
      g.lineStyle(2, 0x334155, 1).strokeRect(r.x, r.y, r.w, r.h);
      this.add.text(r.x + 6, r.y - 14, r.label, { fontFamily: "monospace", fontSize: "11px", color: "#94a3b8" });
      const seats: { x: number; y: number }[] = [];
      if (key === "lounge") {
        // sofa
        g.fillStyle(0x7c2d12, 1).fillRoundedRect(r.x + 30, r.y + 90, 150, 34, 6);
        g.fillStyle(0x9a3412, 1).fillRoundedRect(r.x + 30, r.y + 80, 150, 16, 6);
        for (let i = 0; i < 4; i++) seats.push({ x: r.x + 50 + i * 36, y: r.y + 92 });
        // coffee machine
        g.fillStyle(0x475569, 1).fillRect(r.x + 230, r.y + 30, 26, 40);
        g.fillStyle(0xf59e0b, 1).fillRect(r.x + 238, r.y + 40, 10, 6);
        this.add.text(r.x + 222, r.y + 74, "café", { fontFamily: "monospace", fontSize: "9px", color: "#cbd5e1" });
      } else {
        const cols = key === "meeting" ? 3 : 3;
        for (let i = 0; i < 6; i++) {
          const dx = r.x + 30 + (i % cols) * 92, dy = r.y + 40 + Math.floor(i / cols) * 78;
          g.fillStyle(0x3f3f46, 1).fillRect(dx, dy + 18, 56, 26); // desk
          g.fillStyle(key === "qa" ? 0x0ea5e9 : 0x64748b, 1).fillRect(dx + 20, dy + 8, 16, 12); // monitor
          seats.push({ x: dx + 28, y: dy + 2 });
        }
        if (key === "meeting") {
          g.fillStyle(0x52525b, 1).fillRoundedRect(r.x + 210, r.y + 60, 70, 100, 8);
        }
        if (key === "qa") {
          this.lamp = this.add.rectangle(r.x + r.w - 24, r.y + 16, 12, 12, 0x38bdf8).setAlpha(0.3);
        }
      }
      this.seats[key] = seats;
    });
    this.sync(this.agents);
  }

  sync(agents: Agent[]) {
    this.agents = agents;
    if (!this.sys.isActive()) return;
    const taken: Record<string, number> = { dev: 0, meeting: 0, lounge: 0, qa: 0 };
    agents.forEach((a) => {
      let sp = this.sprites.get(a.name);
      if (!sp) sp = this.spawn(a);
      const room = a.state === "TESTING" ? "qa" : a.state === "IDLE" && a.room !== "lounge" && Math.random() < 0 ? "lounge" : a.room;
      const idx = taken[room] ?? 0;
      taken[room] = idx + 1;
      const seat = this.seats[room][idx % this.seats[room].length] ?? { x: 100, y: 100 };
      sp.seat = seat;
      sp.state = a.state;
      sp.badge.setText(a.state === "BLOCKED" ? "!" : a.state === "WORKING" ? "…" : a.state === "TESTING" ? "⚗" : "");
      sp.badge.setColor(a.state === "BLOCKED" ? "#f87171" : a.state === "TESTING" ? "#38bdf8" : "#e2e8f0");
      sp.label.setText(a.name.replace(" Loompa", ""));
      this.tweens.add({ targets: sp.root, x: seat.x, y: seat.y, duration: 900, ease: "Sine.easeInOut" });
    });
    this.sprites.forEach((sp, name) => { if (!agents.find((a) => a.name === name)) { sp.root.destroy(); this.sprites.delete(name); } });
  }

  private spawn(a: Agent): Sprite {
    const color = ROLE_COLORS[a.role] ?? 0xe2e8f0;
    const textureKey = `loompa-${a.role}`;
    const hasArt = this.textures.exists(textureKey);
    const root = this.add.container(100, 100);
    const shadow = this.add.ellipse(0, 22, 20, 6, 0x000000, 0.35);
    const highlight = this.add.rectangle(0, hasArt ? 4 : 6, hasArt ? 36 : 26, 42).setStrokeStyle(2, 0xffffff, 0);
    const extras: Phaser.GameObjects.GameObject[] = [];
    let body: Phaser.GameObjects.Rectangle | Phaser.GameObjects.Image;
    let badgeBase: number;
    if (hasArt) {
      body = this.add.image(0, 20, textureKey).setOrigin(0.5, 1);
      badgeBase = -26;
    } else {
      body = this.add.rectangle(0, 6, 14, 18, color).setStrokeStyle(1, 0x0b1220);
      const head = this.add.rectangle(0, -8, 12, 12, 0xfde68a).setStrokeStyle(1, 0x0b1220);
      const hair = this.add.rectangle(0, -13, 12, 4, 0x22c55e);
      const eyeL = this.add.rectangle(-3, -8, 2, 2, 0x0b1220);
      const eyeR = this.add.rectangle(3, -8, 2, 2, 0x0b1220);
      extras.push(head, hair, eyeL, eyeR);
      badgeBase = -30;
    }
    const badge = this.add.text(0, badgeBase, "", { fontFamily: "monospace", fontSize: "12px", color: "#e2e8f0", fontStyle: "bold" }).setOrigin(0.5);
    const label = this.add.text(0, 26, "", { fontFamily: "monospace", fontSize: "9px", color: "#cbd5e1" }).setOrigin(0.5, 0);
    root.add([shadow, highlight, body, ...extras, badge, label]);
    root.setSize(hasArt ? 32 : 24, 40).setInteractive({ useHandCursor: true });
    root.on("pointerdown", () => this.onSelect(a.name));
    root.on("pointerover", () => highlight.setStrokeStyle(2, 0xffffff, 1));
    root.on("pointerout", () => highlight.setStrokeStyle(2, 0xffffff, 0));
    const sp: Sprite = { root, body, badge, label, badgeBase, state: a.state, t: Math.random() * 10, home: { x: 100, y: 100 }, seat: { x: 100, y: 100 } };
    this.sprites.set(a.name, sp);
    return sp;
  }

  update(_time: number, delta: number) {
    const dt = delta / 1000;
    let testing = false;
    this.sprites.forEach((sp) => {
      sp.t += dt;
      if (sp.state === "WORKING") {
        sp.body.setScale(1, 1 + Math.sin(sp.t * 14) * 0.04);
        sp.badge.setY(sp.badgeBase + Math.sin(sp.t * 8) * 2);
      } else if (sp.state === "BLOCKED") {
        sp.badge.setY(sp.badgeBase - 2 + Math.abs(Math.sin(sp.t * 4)) * -6);
        sp.badge.setScale(1 + Math.sin(sp.t * 6) * 0.15);
      } else if (sp.state === "TESTING") {
        testing = true;
        sp.badge.setAlpha(0.5 + Math.abs(Math.sin(sp.t * 5)) * 0.5);
      } else {
        sp.body.setScale(1, 1 + Math.sin(sp.t * 1.5) * 0.02); // breathing
        sp.badge.setAlpha(1);
      }
    });
    if (this.lamp) this.lamp.setAlpha(testing ? 0.4 + Math.abs(Math.sin(_time / 150)) * 0.6 : 0.25);
  }
}
