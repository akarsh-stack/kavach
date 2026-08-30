import { rs } from "../api.js";

/**
 * The argument, stated before any data.
 *
 * Everything below this band is evidence for one claim, and until this existed
 * the claim was only reachable by clicking to a second tab and reading a bar
 * chart. A reader who gives the page fifteen seconds saw "Recovered ₹43,862"
 * with nothing to compare it against, and left knowing nothing.
 *
 * Both figures are read from the run rather than written down, so this cannot
 * drift from the evidence it is summarising. If the ablation is missing from a
 * run, the band renders nothing rather than half an argument.
 */
export default function Thesis({ policies, batchSize }) {
  if (!policies) return null;

  const by = Object.fromEntries(policies.map((p) => [p.policy, p]));
  const agent = by.agent;
  const naive = by.naive_llm;
  if (!agent || !naive) return null;

  const money = (paise) =>
    `${paise < 0 ? "−" : "+"}₹${rs(Math.abs(paise))}`;

  return (
    <section className="thesis rise">
      <p className="thesis-lede">
        The same language model, the same {batchSize} failed payments, the same
        random seed. The only difference is whether a policy layer sits between
        the model and the payment rail.
      </p>

      <div className="thesis-pair">
        <div className="thesis-side" data-tone="good">
          <span className="thesis-label">With the policy layer</span>
          <span className="thesis-figure">{money(agent.ledger.net_paise)}</span>
          <span className="thesis-sub">
            {agent.ledger.policy_violations} compliance violations
          </span>
        </div>

        <div className="thesis-side" data-tone="bad">
          <span className="thesis-label">Without it</span>
          <span className="thesis-figure">{money(naive.ledger.net_paise)}</span>
          <span className="thesis-sub">
            {naive.ledger.policy_violations} compliance violations
          </span>
        </div>
      </div>

      <p className="thesis-kicker">
        Unguarded, it recovers <em>more</em> gross revenue —{" "}
        ₹{rs(naive.ledger.gross_recovered_paise)} against ₹
        {rs(agent.ledger.gross_recovered_paise)} — and still ends up losing
        money, because it retries fraud declines and messages people at 3am
        until they leave. <strong>The difference is not the model.</strong> It is
        what the model was allowed to do.
      </p>
    </section>
  );
}
