import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function GET() {
  const groqKey = process.env.GROQ_API_KEY || '';
  const results: Record<string, any> = { groq_key_prefix: groqKey.slice(0, 8) };

  // Load first 50 segments and build a realistic diarize prompt
  try {
    const whisperPath = path.resolve('./jobs/smoking_supermarket/s01/e01/states/state_whisper.json');
    const segs: any[] = JSON.parse(fs.readFileSync(whisperPath, 'utf-8')).slice(0, 50);
    const chunk = segs.map((s, i) => ({ i: s.id ?? i, s: Math.round(s.start * 10) / 10, e: Math.round(s.end * 10) / 10, t: s.text }));
    const prompt = `Assign speaker names and emotions to these ${chunk.length} lines from a Japanese show. Return JSON: {"lines":[{"i":0,"c":"Character","em":"neutral","tp":"speech"}...],"songs":[]}. Each "lines" entry maps to one input line. Input: ${JSON.stringify(chunk)}`;

    results.prompt_chars = prompt.length;
    results.prompt_tokens_est = Math.round(prompt.length / 4);

    const start = Date.now();
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 120000);

    try {
      const res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
        method: 'POST',
        signal: ctrl.signal,
        headers: { 'Authorization': `Bearer ${groqKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'llama-3.3-70b-versatile',
          messages: [{ role: 'user', content: prompt }],
          response_format: { type: 'json_object' },
          max_tokens: 4096,
        }),
      });
      clearTimeout(t);
      results.groq_status = res.status;
      results.groq_elapsed_ms = Date.now() - start;
      if (res.ok) {
        const d = await res.json();
        results.groq_finish = d.choices?.[0]?.finish_reason;
        results.groq_tokens = d.usage?.total_tokens;
        results.groq_queue_time = d.usage?.queue_time;
      } else {
        results.groq_error_body = (await res.text()).slice(0, 300);
      }
    } catch (e: any) {
      clearTimeout(t);
      results.groq_error = e.message;
      results.groq_error_name = e.name;
      results.groq_cause = e.cause?.message || e.cause?.code || String(e.cause);
      results.groq_elapsed_ms = Date.now() - start;
    }
  } catch (e: any) {
    results.setup_error = e.message;
  }

  return NextResponse.json(results);
}
