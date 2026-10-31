"use client";

import { useEffect, useState } from "react";

function greetingsForHour(hour: number): string[] {
  if (hour >= 5 && hour < 12) {
    return ["早上好，今天想学点什么？", "一起把今天的问题弄明白。", "今天，想聊哪一道题？"];
  }
  if (hour >= 12 && hour < 17) {
    return ["下午好，想聊些什么？", "把卡住的那一步告诉我。", "今天，想聊哪一道题？"];
  }
  if (hour >= 17 && hour < 22) {
    return ["晚上好，今天想学点什么？", "一起看看今天的问题。", "今天，想聊哪一道题？"];
  }
  return ["夜深了，记下你的思路吧。", "有什么问题想一起看看？", "今天，想聊哪一道题？"];
}

/** A presentation-only welcome. The fixed first line also keeps hydration stable. */
export default function AnimatedGreeting({
  initialGreeting = "今天，想聊哪一道题？",
  className = "",
  translate = (value: string) => value,
}: {
  initialGreeting?: string;
  className?: string;
  translate?: (value: string) => string;
}) {
  const [greeting, setGreeting] = useState(initialGreeting);

  useEffect(() => {
    let index = 0;
    let previous = initialGreeting;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      const greetings = greetingsForHour(new Date().getHours());
      let next = greetings[index++ % greetings.length];
      if (next === previous) next = greetings[index++ % greetings.length];
      previous = next;
      setGreeting(next);
    }, 10 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [initialGreeting]);

  return (
    <h1 className={`al-study-greeting ${className}`}>
      <span key={greeting} className="al-study-greeting-line">{translate(greeting)}</span>
    </h1>
  );
}
