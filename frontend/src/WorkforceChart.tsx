import { useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import {
  boundedObservations, formatSignedChange, toChartRows,
  type LifecycleFilter,
} from "./workforceData.ts";
import { syntheticEvents } from "./syntheticData.ts";

const filters: readonly { id: LifecycleFilter; label: string }[] = [
  { id: "all", label: "All events" },
  { id: "announced", label: "Announced" },
  { id: "planned", label: "Planned" },
  { id: "completed", label: "Completed" },
];
const SERIES = [
  { key: "announced", title: "Announced · not realised", color: "#d99b3e" },
  { key: "planned", title: "Planned · not realised", color: "#6979c6" },
  { key: "completed", title: "Completed", color: "#148f80" },
] as const;

export function WorkforceChart() {
  const [filter, setFilter] = useState<LifecycleFilter>("all");
  const chartData = useMemo(() => toChartRows(syntheticEvents, filter), [filter]);
  const bounds = useMemo(() => boundedObservations(syntheticEvents, filter), [filter]);

  return (
    <main className="page">
      <header className="masthead">
        <div className="eyebrow">Corporate intelligence / engineering sample 07</div>
        <h1>Workforce change evidence</h1>
        <p className="subtitle">
          A focused React + TypeScript + Recharts example of financially responsible
          data visualisation. All events below are invented.
        </p>
      </header>

      <section className="panel" aria-labelledby="chart-title">
        <div className="panel-heading">
          <div>
            <h2 id="chart-title">Individual workforce events</h2>
            <p>Signed reported changes by event — not a net company headcount series.</p>
          </div>
          <span className="count">{chartData.length} exact observations</span>
        </div>
        <div className="filters" role="group" aria-label="Filter events by lifecycle">
          {filters.map((option) => (
            <button
              key={option.id}
              className={filter === option.id ? "filter active" : "filter"}
              type="button"
              aria-pressed={filter === option.id}
              onClick={() => setFilter(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="chart" role="img" aria-label="Bar chart of individual signed workforce events, separated by lifecycle">
          {chartData.length ? (
            <ResponsiveContainer width="100%" height={350}>
              <BarChart data={chartData} margin={{ top: 8, right: 22, left: 8, bottom: 52 }} barGap={4}>
                <CartesianGrid vertical={false} strokeDasharray="3 5" stroke="#e4e8f0" />
                <XAxis dataKey="label" angle={-32} textAnchor="end" height={96} tick={{ fontSize: 11, fill: "#46566c" }} interval={0} />
                <YAxis tickFormatter={(value: number) => formatSignedChange(value)} width={68} tick={{ fontSize: 12, fill: "#46566c" }} />
                <Tooltip
                  formatter={(value, name) => [formatSignedChange(Number(value)) + " people", String(name)]}
                  labelStyle={{ color: "#23324c", fontWeight: 600 }}
                />
                <Legend verticalAlign="top" align="right" height={42} wrapperStyle={{ fontSize: 12 }} />
                {SERIES.map((series) => (
                  <Bar key={series.key} dataKey={series.key} name={series.title}
                    fill={series.color} maxBarSize={32} radius={[3, 3, 3, 3]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          ) : <div className="empty" role="status">No exact observations for this filter.</div>}
        </div>
        <p className="method-note">
          Each bar represents one separately reported event. A planned or announced
          reduction is <strong>not</strong> counted as realised. No individual
          events are connected into a fictional time series.
        </p>
      </section>

      <section className="panel bounds-panel" aria-labelledby="bounds-title">
        <div className="panel-heading">
          <div>
            <h2 id="bounds-title">Uncertain quantities</h2>
            <p>Retained as qualified evidence; never plotted as exact values.</p>
          </div>
          <span className="count">{bounds.length} bounded observations</span>
        </div>
        {bounds.length ? (
          <ul className="bounds-list">
            {bounds.map((observation) => (
              <li key={observation.id}>
                <div>
                  <span className="bound-label">{observation.label} people</span>
                  <div className="bound-location">{observation.date} · {observation.geography}</div>
                </div>
                <div className="bound-context">{observation.context}</div>
              </li>
            ))}
          </ul>
        ) : <p className="empty-small" role="status">No bounded evidence for this filter.</p>}
      </section>
      <footer>Self-contained synthetic demonstration · No live API, registry or company data</footer>
    </main>
  );
}
