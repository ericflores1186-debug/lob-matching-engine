import { Fragment } from "react";
import type { Decoded, DecodedField, Issue } from "../api/types";
import { Badge } from "./ui";

const HEADER_TAGS = new Set([8, 9, 35, 49, 56, 34, 52, 43, 97, 122, 50, 57, 115, 128, 141]);
const TRAILER_TAGS = new Set([10]);

function section(field: DecodedField): "Standard header" | "Body" | "Standard trailer" {
  if (field.tag !== null && TRAILER_TAGS.has(field.tag)) return "Standard trailer";
  if (field.tag !== null && HEADER_TAGS.has(field.tag)) return "Standard header";
  return "Body";
}

export function MessageHeader({ decoded, status }: { decoded: Decoded; status: string }) {
  const lengthOk = decoded.body_length.declared === decoded.body_length.computed;
  const checksumOk = decoded.checksum.declared === decoded.checksum.computed;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-4 py-2.5 text-xs">
      <Badge value={status} />
      <span className="font-mono text-slate-200">
        {decoded.msg_type_name ?? "UNKNOWN"} <span className="text-slate-500">(35={decoded.msg_type ?? "?"})</span>
      </span>
      <span className="font-mono text-slate-400">{decoded.begin_string ?? "?"}</span>
      <span className="font-mono text-slate-300">
        {decoded.sender_comp_id ?? "?"} <span className="text-slate-600">→</span> {decoded.target_comp_id ?? "?"}
      </span>
      <span className="font-mono text-slate-400">34={decoded.msg_seq_num ?? "?"}</span>
      <span className={`font-mono ${lengthOk ? "text-slate-500" : "text-rose-400"}`} title="Declared vs computed BodyLength(9)">
        9={decoded.body_length.declared ?? "?"}
        {lengthOk ? " ✓" : ` ≠ ${decoded.body_length.computed ?? "?"}`}
      </span>
      <span className={`font-mono ${checksumOk ? "text-slate-500" : "text-rose-400"}`} title="Declared vs computed CheckSum(10)">
        10={decoded.checksum.declared ?? "?"}
        {checksumOk ? " ✓" : ` ≠ ${decoded.checksum.computed ?? "?"}`}
      </span>
      <span className="font-mono text-slate-600">delim {decoded.delimiter}</span>
    </div>
  );
}

export function IssueList({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) return null;
  return (
    <ul className="space-y-1 border-b border-line px-4 py-2.5">
      {issues.map((issue, i) => (
        <li key={`${issue.code}-${issue.tag ?? "x"}-${i}`} className="flex items-start gap-2 text-xs">
          <span
            className={`mt-0.5 shrink-0 rounded px-1 font-mono text-[10px] font-semibold ${
              issue.severity === "ERROR" ? "bg-rose-500/15 text-rose-300" : issue.severity === "WARN" ? "bg-amber-500/15 text-amber-300" : "bg-sky-500/15 text-sky-300"
            }`}
          >
            {issue.severity}
          </span>
          <span className="font-mono text-slate-400">{issue.code}</span>
          {issue.tag !== null ? <span className="font-mono text-slate-600">tag {issue.tag}</span> : null}
          <span className={issue.severity === "ERROR" ? "text-rose-200" : "text-slate-300"}>{issue.detail}</span>
        </li>
      ))}
    </ul>
  );
}

export function TagDecoderTable({ decoded, maxHeight = "max-h-[520px]" }: { decoded: Decoded; maxHeight?: string }) {
  const details = new Map<number, Issue[]>();
  for (const issue of decoded.issues) {
    if (issue.tag === null) continue;
    details.set(issue.tag, [...(details.get(issue.tag) ?? []), issue]);
  }
  let previous = "";
  return (
    <div className={`${maxHeight} overflow-auto`}>
      <table className="w-full table-fixed text-xs">
        <colgroup>
          <col className="w-16" />
          <col className="w-44" />
          <col className="w-[34%]" />
          <col />
        </colgroup>
        <thead>
          <tr>
            <th className="th">Tag</th>
            <th className="th">Field name</th>
            <th className="th">Raw value</th>
            <th className="th">Human-readable meaning</th>
          </tr>
        </thead>
        <tbody>
          {decoded.fields.map((field) => {
            const group = section(field);
            const showGroup = group !== previous;
            previous = group;
            const fieldIssues = field.tag === null ? [] : details.get(field.tag) ?? [];
            const blocking = field.tag === null || fieldIssues.some((i) => i.severity !== "INFO") || field.issues.length > fieldIssues.length;
            const flagged = field.issues.length > 0 || field.tag === null;
            const tone = flagged && blocking ? "bg-rose-500/[0.08] text-rose-100" : flagged ? "bg-sky-500/[0.06]" : "";
            return (
              <Fragment key={field.index}>
                {showGroup ? (
                  <tr>
                    <td colSpan={4} className="bg-ink-900 px-3 pb-1 pt-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-600">
                      {group}
                    </td>
                  </tr>
                ) : null}
                <tr className={`border-t border-line/50 ${tone}`}>
                  <td className={`td num ${flagged && blocking ? "border-l-2 border-rose-500 text-rose-300" : "border-l-2 border-transparent text-sky-300"}`}>
                    {field.tag ?? "?"}
                  </td>
                  <td className="td truncate font-medium text-slate-300" title={field.name}>
                    {field.name}
                  </td>
                  <td className="td num break-all text-slate-100">{field.value === "" ? <span className="italic text-rose-400">(empty)</span> : field.value}</td>
                  <td className="td text-slate-400">
                    <div>{field.meaning ?? <span className="text-slate-600">—</span>}</div>
                    {fieldIssues.map((issue, i) => (
                      <div key={i} className={`mt-0.5 text-[11px] ${issue.severity === "INFO" ? "text-sky-300" : "text-rose-300"}`}>
                        {issue.code}: {issue.detail}
                      </div>
                    ))}
                  </td>
                </tr>
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
