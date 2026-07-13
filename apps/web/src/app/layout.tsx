import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Sherlock — Candidate Identifier",
  description:
    "Testing dashboard for the Sherlock candidate-identification engine: run scenarios, watch the meeting, and inspect the Engine's live confidence.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      {/* isolation: isolate - required by @base-ui/react so portaled
          popups (dialogs, etc.) always stack above page content
          regardless of any z-index elsewhere. */}
      <body className="root min-h-full flex flex-col isolate">{children}</body>
    </html>
  );
}
