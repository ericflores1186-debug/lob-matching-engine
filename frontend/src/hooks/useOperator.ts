import { useCallback, useState } from "react";
import type { Role } from "../api/types";

export interface Operator {
  role: Role;
  makerId: string;
  checkerId: string;
}

const STORAGE_KEY = "lobx.operator";
const DEFAULT_OPERATOR: Operator = { role: "MAKER", makerId: "ops.maker.jlee", checkerId: "ops.checker.amorgan" };

function load(): Operator {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_OPERATOR;
    const parsed = JSON.parse(raw) as Partial<Operator>;
    return {
      role: parsed.role === "CHECKER" ? "CHECKER" : "MAKER",
      makerId: parsed.makerId || DEFAULT_OPERATOR.makerId,
      checkerId: parsed.checkerId || DEFAULT_OPERATOR.checkerId,
    };
  } catch {
    return DEFAULT_OPERATOR;
  }
}

export function useOperator(): [Operator, (next: Partial<Operator>) => void] {
  const [operator, setOperator] = useState<Operator>(load);
  const update = useCallback((next: Partial<Operator>) => {
    setOperator((current) => {
      const merged = { ...current, ...next };
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
      } catch {
        // Storage can be unavailable (private mode); the session value still applies
      }
      return merged;
    });
  }, []);
  return [operator, update];
}

export function activeOperatorId(operator: Operator): string {
  return operator.role === "MAKER" ? operator.makerId : operator.checkerId;
}
