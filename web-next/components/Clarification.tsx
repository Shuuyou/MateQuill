"use client";
import { useState } from "react";
import type { Question } from "@/lib/types";
import Character from "./Character";

export default function Clarification({
  query,
  questions,
  busy,
  onAnswer,
  onBack,
}: {
  query: string;
  questions: Question[];
  busy: boolean;
  onAnswer: (answers: Record<string, string>) => void;
  onBack: () => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  return (
    <section className="mq-clarification mq-enter">
      <div className="mq-receipt">
        <p>{query}</p>
        <button type="button" onClick={onBack}>
          修改需求
        </button>
      </div>
      <div className="mq-clarify-heading">
        <Character state={busy ? "thinking" : "waiting"} />
        <div>
          <h1>再确认一下，就开始。</h1>
          <p>让这次内容更贴近你眼前的顾客。</p>
        </div>
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onAnswer(answers);
        }}
      >
        {questions.map((question) => (
          <fieldset className="mq-question" key={question.field}>
            <legend>{question.label}</legend>
            {question.options.length ? (
              <div className="mq-options">
                {question.options.map((option) => (
                  <label
                    className={
                      answers[question.field] === option.value
                        ? "is-selected"
                        : ""
                    }
                    key={option.value}
                  >
                    <input
                      type="radio"
                      name={question.field}
                      value={option.value}
                      checked={answers[question.field] === option.value}
                      onChange={() =>
                        setAnswers((previous) => ({
                          ...previous,
                          [question.field]: option.value,
                        }))
                      }
                    />
                    <span>{option.label}</span>
                  </label>
                ))}
              </div>
            ) : (
              <input
                aria-label={question.label}
                value={answers[question.field] ?? ""}
                onChange={(event) =>
                  setAnswers((previous) => ({
                    ...previous,
                    [question.field]: event.target.value,
                  }))
                }
              />
            )}
          </fieldset>
        ))}
        <button
          className="mq-primary"
          type="submit"
          disabled={
            busy ||
            questions.some((question) => !answers[question.field]?.trim())
          }
        >
          {busy ? "准备内容中…" : "就这样，开始创作"}
          <span aria-hidden="true">↗</span>
        </button>
      </form>
    </section>
  );
}
