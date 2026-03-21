"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getCatalog } from "../../lib/api";
import type { Catalog } from "../../lib/types";

export default function CatalogPage() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);

  useEffect(() => {
    getCatalog().then(setCatalog).catch(console.error);
  }, []);

  return (
    <main className="min-h-screen bg-slate-950 px-8 py-10 text-slate-100">
      <div className="mx-auto max-w-6xl">
        <div className="mb-8 flex items-center justify-between">
          <div>
            <div className="text-sm uppercase tracking-[0.3em] text-sky-300">Catalog</div>
            <h1 className="mt-2 text-3xl font-semibold">Skill / Tool / Learning Focus</h1>
          </div>
          <Link href="/" className="rounded-xl border border-slate-700 px-4 py-2 text-sm">
            返回工作台
          </Link>
        </div>

        <section className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
            <h2 className="text-xl font-semibold">Skills</h2>
            <div className="mt-4 space-y-4">
              {catalog?.skills.map((skill) => (
                <div key={skill.id} className="rounded-2xl border border-slate-800 p-4">
                  <div className="flex items-center justify-between">
                    <div className="font-medium">{skill.name}</div>
                    <div className="text-xs text-slate-400">{skill.category}</div>
                  </div>
                  <div className="mt-2 text-sm text-slate-400">{skill.description}</div>
                  <div className="mt-3 text-xs text-sky-300">Tools: {skill.tools.join(", ")}</div>
                  <div className="mt-2 text-xs text-slate-500">Learning: {skill.learning_focus.join(" / ")}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-6">
            <section className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
              <h2 className="text-xl font-semibold">Tools</h2>
              <div className="mt-4 space-y-3">
                {catalog?.tools.map((tool) => (
                  <div key={tool.name} className="rounded-2xl border border-slate-800 p-4">
                    <div className="font-medium">{tool.name}</div>
                    <div className="mt-2 text-sm text-slate-400">{tool.description}</div>
                    <div className="mt-2 text-xs text-sky-300">Skill: {tool.skill_id}</div>
                  </div>
                ))}
              </div>
            </section>

            <section className="rounded-3xl border border-slate-800 bg-slate-900 p-5">
              <h2 className="text-xl font-semibold">LangChain 学习点</h2>
              <div className="mt-4 space-y-3">
                {catalog?.learning_focus.map((item) => (
                  <div key={item.name} className="rounded-2xl border border-slate-800 p-4">
                    <div className="font-medium">{item.name}</div>
                    <div className="mt-2 text-sm text-slate-400">{item.description}</div>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </section>
      </div>
    </main>
  );
}
