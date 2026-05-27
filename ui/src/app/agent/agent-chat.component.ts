import { Component, ChangeDetectionStrategy, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { DownloadsService } from '../services/downloads.service';

type ChatTurn = {
  role: 'user' | 'agent';
  text?: string;
  tool?: { name: string; args: Record<string, unknown> };
  toolResult?: { name: string; response: Record<string, unknown> };
  confirmation?: ConfirmationRequest;
};

type ConfirmationRequest = {
  function_call_id: string;
  name: string;
  hint?: string;
  payload?: Record<string, unknown>;
  resolved?: 'approved' | 'rejected';
};

@Component({
  selector: 'app-agent-chat',
  standalone: true,
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="agent-chat-toggle" *ngIf="enabled()">
      <button class="btn btn-primary btn-sm" (click)="open.set(!open())">
        {{ open() ? 'Close chat' : 'Open agent' }}
      </button>
    </div>
    <div class="agent-chat-panel" *ngIf="enabled() && open()">
      <div class="agent-chat-log">
        <div *ngFor="let t of turns()" class="agent-turn agent-turn-{{t.role}}">
          <ng-container *ngIf="t.text">{{ t.text }}</ng-container>
          <ng-container *ngIf="t.tool">
            <em>→ {{ t.tool.name }}({{ argSummary(t.tool.args) }})</em>
          </ng-container>
          <ng-container *ngIf="t.toolResult">
            <em>← {{ t.toolResult.name }}: {{ resultSummary(t.toolResult.response) }}</em>
          </ng-container>
          <div *ngIf="t.confirmation" class="agent-confirm">
            <strong>{{ t.confirmation.hint || 'Confirm?' }}</strong>
            <pre>{{ t.confirmation.payload | json }}</pre>
            <button class="btn btn-success btn-sm"
                    [disabled]="t.confirmation.resolved || sending()"
                    (click)="resolveConfirmation(t.confirmation, true)">Approve</button>
            <button class="btn btn-danger btn-sm"
                    [disabled]="t.confirmation.resolved || sending()"
                    (click)="resolveConfirmation(t.confirmation, false)">Reject</button>
            <span *ngIf="t.confirmation.resolved"> — {{ t.confirmation.resolved }}</span>
          </div>
        </div>
      </div>
      <form class="agent-chat-input" (ngSubmit)="send()">
        <input type="text" [(ngModel)]="draft" name="draft"
               placeholder="Ask the agent…" [disabled]="sending()" />
        <button class="btn btn-primary" type="submit" [disabled]="!draft.trim() || sending()">
          Send
        </button>
      </form>
    </div>
  `,
  styles: [`
    .agent-chat-toggle { position: fixed; right: 1rem; bottom: 1rem; z-index: 1000; }
    .agent-chat-panel {
      position: fixed; right: 1rem; bottom: 4rem; z-index: 1000;
      width: 24rem; max-height: 32rem;
      display: flex; flex-direction: column;
      background: var(--bs-body-bg, #fff); border: 1px solid #ccc;
      border-radius: 0.4rem; box-shadow: 0 0.2rem 0.6rem rgba(0,0,0,0.2);
    }
    .agent-chat-log { flex: 1; overflow-y: auto; padding: 0.5rem; font-size: 0.85rem; }
    .agent-turn { margin-bottom: 0.4rem; }
    .agent-turn-user { text-align: right; }
    .agent-turn-user::before { content: "▸ "; opacity: 0.6; }
    .agent-confirm { padding: 0.4rem; background: #fffbea; border: 1px solid #eaca57; margin-top: 0.3rem; }
    .agent-confirm pre { font-size: 0.75rem; max-height: 6rem; overflow: auto; margin: 0.2rem 0; }
    .agent-chat-input { display: flex; padding: 0.4rem; gap: 0.3rem; border-top: 1px solid #ddd; }
    .agent-chat-input input { flex: 1; padding: 0.3rem; border: 1px solid #ccc; border-radius: 0.2rem; }
  `],
})
export class AgentChatComponent {
  private downloads = inject(DownloadsService);

  open = signal(false);
  sending = signal(false);
  turns = signal<ChatTurn[]>([]);
  draft = '';

  enabled = computed(() => this.downloads.configuration['AGENT_ENABLED'] === true);

  argSummary(args: Record<string, unknown>): string {
    return Object.entries(args).slice(0, 3).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(', ');
  }

  resultSummary(r: Record<string, unknown>): string {
    if (r['status']) return String(r['status']);
    return JSON.stringify(r).slice(0, 80);
  }

  async send(): Promise<void> {
    const text = this.draft.trim();
    if (!text) return;
    this.appendTurn({ role: 'user', text });
    this.draft = '';
    this.sending.set(true);
    try {
      await this.stream('agent/chat', { message: text });
    } finally {
      this.sending.set(false);
    }
  }

  async resolveConfirmation(c: ConfirmationRequest, approve: boolean): Promise<void> {
    c.resolved = approve ? 'approved' : 'rejected';
    this.turns.set([...this.turns()]);  // trigger CD
    this.sending.set(true);
    try {
      await this.stream('agent/confirm', {
        function_call_id: c.function_call_id,
        name: c.name,
        confirmed: approve,
        payload: c.payload,
      });
    } finally {
      this.sending.set(false);
    }
  }

  private async stream(path: string, body: unknown): Promise<void> {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) {
      this.appendTurn({ role: 'agent', text: `error: HTTP ${res.status}` });
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const data = line.slice(5).trim();
          if (!data) continue;
          try {
            this.handleEvent(JSON.parse(data));
          } catch { /* ignore parse errors */ }
        }
      }
    }
  }

  private handleEvent(ev: Record<string, unknown>): void {
    if (typeof ev['text'] === 'string') {
      this.appendTurn({ role: 'agent', text: ev['text'] as string });
    }
    const fcs = ev['function_calls'] as Array<{id: string; name: string; args: Record<string, unknown>}> | undefined;
    if (fcs) {
      for (const fc of fcs) {
        this.appendTurn({ role: 'agent', tool: { name: fc.name, args: fc.args } });
      }
    }
    const frs = ev['function_responses'] as Array<{id: string; name: string; response: Record<string, unknown>}> | undefined;
    if (frs) {
      for (const fr of frs) {
        if (fr.response && fr.response['status'] === 'awaiting_confirmation') {
          this.appendTurn({
            role: 'agent',
            confirmation: {
              function_call_id: fr.id,
              name: fr.name,
              hint: (ev['request_input'] as {message?: string})?.message,
              payload: (fr.response['payload'] as Record<string, unknown>) || {},
            },
          });
        } else {
          this.appendTurn({ role: 'agent', toolResult: { name: fr.name, response: fr.response } });
        }
      }
    }
    const ri = ev['request_input'] as {interrupt_id?: string; message?: string; payload?: Record<string, unknown>} | undefined;
    if (ri && ri.interrupt_id && !frs) {
      // RequestInput arrived without an accompanying function_response — surface it raw.
      this.appendTurn({
        role: 'agent',
        confirmation: {
          function_call_id: ri.interrupt_id,
          name: '(unknown)',
          hint: ri.message,
          payload: ri.payload || {},
        },
      });
    }
  }

  private appendTurn(t: ChatTurn): void {
    this.turns.set([...this.turns(), t]);
  }
}
