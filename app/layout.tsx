import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Yanflix Dubbing Studio",
  description: "AI-powered dubbing pipeline — Demucs · Whisper · Ollama · IndexTTS-2",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}