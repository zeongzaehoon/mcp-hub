-- biz: deterministic synthetic business dataset for the sample ClickHouse datasource.
-- Three column-mode tenants share every business table, separated by company_id:
--   'ftg'          bug-bounty platform            - steady growth
--   'ftg-academy'  security training              - flat, seasonal around course intakes (Mar / Sep)
--   'ftg-cloud'    cloud + compliance consulting  - fast growth from a small base
-- Tables: customers, revenue, expenses, campaigns, marketing_daily, headcount_monthly, cash_monthly
-- (all tenant-scoped) and fx_rates, which has NO company_id and must stay invisible to column-mode tenants.
-- Transactional range: 2025-01-01 .. 2026-10-31 (customers may be acquired earlier = installed base).
-- Everything is derived from cityHash64(...) pseudo-randomness over numbers() - no rand()/now() -
-- so every fresh container produces identical data. Amounts are KRW.
-- Patterns built in: Q4 high with Jan/Aug dips, weekday effect, marketing spend -> leads with a 1-3 day
-- lag, CTR/CVR differing by channel, churn clustered in 2025-08 and 2026-02, a refund spike
-- (ftg, 2026-03-09..22) and an infrastructure cost jump (ftg-cloud, from 2026-06-01).
-- No personal data: customers / vendors / campaigns are synthetic codes only.

CREATE DATABASE IF NOT EXISTS biz;

-- ───────────────────────── customers ─────────────────────────
CREATE TABLE IF NOT EXISTS biz.customers
(
    company_id          LowCardinality(String),
    customer_id         String,
    segment             LowCardinality(String),
    industry            LowCardinality(String),
    region              LowCardinality(String),
    acquisition_channel LowCardinality(String),
    acquired_at         Date,
    churned_at          Nullable(Date)
) ENGINE = MergeTree ORDER BY (company_id, customer_id);

INSERT INTO biz.customers
WITH
    ['ftg', 'ftg-academy', 'ftg-cloud'] AS cos,
    multiIf(number < 700, 1, number < 1200, 2, 3) AS ci,
    (cityHash64('cust-acq', number) % 1000000) / 1000000. AS u_acq,
    (cityHash64('cust-seg', number) % 1000000) / 1000000. AS u_seg,
    (cityHash64('cust-ind', number) % 1000000) / 1000000. AS u_ind,
    (cityHash64('cust-reg', number) % 1000000) / 1000000. AS u_reg,
    (cityHash64('cust-chn', number) % 1000000) / 1000000. AS u_chn,
    (cityHash64('cust-churn', number) % 1000000) / 1000000. AS u_ch,
    (cityHash64('cust-churn-when', number) % 1000000) / 1000000. AS u_cw,
    -- acquisition window ends 2026-10-31; ftg/academy have a 1-year installed base, cloud only 6 months
    multiIf(ci = 3, 669 + 180, 669 + 365) AS span,
    -- position inside the window: cloud density grows (sqrt), ftg mildly, academy flat
    multiIf(ci = 3, sqrt(u_acq), ci = 1, pow(u_acq, 0.85), u_acq) AS pos,
    toDate('2026-10-31') - span + toUInt32(pos * span) AS acquired,
    [[0.25, 0.65, 0.90, 1.0], [0.15, 0.50, 0.70, 1.0], [0.30, 0.60, 0.85, 1.0]][ci] AS seg_cum,
    ['enterprise', 'smb', 'startup', 'public'][arrayFirstIndex(x -> u_seg < x, seg_cum)] AS seg,
    multiIf(u_cw < 0.30, toDate('2025-08-01') + toUInt32(u_cw * 100),              -- cluster: 2025-08
            u_cw < 0.60, toDate('2026-02-01') + toUInt32((u_cw - 0.30) * 90),      -- cluster: 2026-02
            toDate('2025-01-01') + toUInt32((u_cw - 0.60) * 2.5 * 668)) AS churn_day
SELECT
    cos[ci] AS company_id,
    concat('C-', leftPad(toString(number + 1), 6, '0')) AS customer_id,
    seg AS segment,
    if(seg = 'public', 'public-sector',
       ['fintech', 'gaming', 'ecommerce', 'saas', 'manufacturing', 'healthcare', 'telecom', 'public-sector'][toUInt32(u_ind * 7) + 1]) AS industry,
    ['seoul', 'gyeonggi', 'busan', 'daegu', 'other', 'overseas'][arrayFirstIndex(x -> u_reg < x, [0.50, 0.70, 0.78, 0.83, 0.93, 1.0])] AS region,
    ['search', 'social', 'display', 'email', 'event', 'content', 'referral', 'direct'][arrayFirstIndex(x -> u_chn < x, [0.22, 0.34, 0.40, 0.48, 0.60, 0.70, 0.85, 1.0])] AS acquisition_channel,
    acquired AS acquired_at,
    if(u_ch < 0.16 AND churn_day > acquired + 90, churn_day, NULL) AS churned_at
FROM numbers(1500);

-- ───────────────────────── revenue ─────────────────────────
CREATE TABLE IF NOT EXISTS biz.revenue
(
    company_id    LowCardinality(String),
    txn_id        String,
    txn_date      Date,
    customer_id   String,
    product       LowCardinality(String),
    plan          LowCardinality(String),
    amount        Float64,
    discount_rate Float64,
    status        Enum8('paid' = 1, 'refunded' = 2, 'pending' = 3)
) ENGINE = MergeTree ORDER BY (company_id, txn_date, txn_id);

INSERT INTO biz.revenue
WITH
    toDate('2025-01-01') AS d0,
    multiIf(r.company_id = 'ftg', 1, r.company_id = 'ftg-academy', 2, 3) AS ci,
    (cityHash64('txn-prod', r.company_id, r.d, r.k) % 1000000) / 1000000. AS u_prod,
    (cityHash64('txn-plan', r.company_id, r.d, r.k) % 1000000) / 1000000. AS u_plan,
    (cityHash64('txn-amt',  r.company_id, r.d, r.k) % 1000000) / 1000000. AS u_amt,
    (cityHash64('txn-disc', r.company_id, r.d, r.k) % 1000000) / 1000000. AS u_disc,
    (cityHash64('txn-stat', r.company_id, r.d, r.k) % 1000000) / 1000000. AS u_stat,
    multiIf(ci = 1, ['bounty-platform', 'managed-triage', 'pentest-service', 'vuln-scanner', 'bug-bounty-program'],
            ci = 2, ['web-hacking-course', 'cloud-security-course', 'ctf-bootcamp', 'corporate-training', 'certification-prep'],
                    ['csap-consulting', 'iso27001-consulting', 'cloud-migration', 'managed-security', 'compliance-audit']) AS products,
    multiIf(ci = 2,               [0.35, 0.55, 0.65, 1.0],   -- academy sells many one-off courses
            c.segment = 'enterprise', [0.15, 0.45, 0.85, 1.0],
            c.segment = 'public',     [0.20, 0.50, 0.70, 1.0],
            c.segment = 'smb',        [0.50, 0.85, 0.92, 1.0],
                                      [0.65, 0.90, 0.93, 1.0]) AS plan_cum,
    arrayFirstIndex(x -> u_plan < x, plan_cum) AS pi,
    ['basic', 'pro', 'enterprise', 'one-off'][pi] AS plan_name,
    multiIf(pi = 1,   300000 + u_amt *   500000,
            pi = 2,   800000 + u_amt *  1600000,
            pi = 3,  2500000 + u_amt * 10000000,
                      400000 + u_amt *  3600000) AS base_amt,
    multiIf(ci = 1, 1.0, ci = 2, 0.7, 1.3) AS co_mult,
    multiIf(c.segment = 'enterprise', 1.3, c.segment = 'public', 1.1, c.segment = 'smb', 0.9, 0.7) AS seg_mult,
    -- anomaly: refund spike on ftg in mid-March 2026
    if(r.company_id = 'ftg' AND r.d BETWEEN toDate('2026-03-09') AND toDate('2026-03-22'), 0.30, 0.03) AS p_refund,
    if(r.d >= toDate('2026-10-18'), 0.20, 0.015) AS p_pending
SELECT
    r.company_id,
    concat('T-', substring(hex(cityHash64('txn-id', r.company_id, r.d, r.k)), 1, 12)) AS txn_id,
    r.d AS txn_date,
    r.customer_id,
    products[toUInt32(u_prod * 5) + 1] AS product,
    plan_name AS plan,
    greatest(100000., round(base_amt * co_mult * seg_mult, -3)) AS amount,
    if(u_disc < 0.6, 0., round((u_disc - 0.6) / 0.4 * 0.3, 2)) AS discount_rate,
    multiIf(u_stat < p_refund, 'refunded', u_stat < p_refund + p_pending, 'pending', 'paid') AS status
FROM
(
    SELECT
        e.company_id,
        e.d,
        k,
        e.ids[toUInt32(cityHash64('txn-cust', e.company_id, e.d, k) % length(e.ids)) + 1] AS customer_id
    FROM
    (
        -- customers active on each day, and the number of transactions that day
        SELECT
            c.company_id,
            days.d,
            groupArray(c.customer_id) AS ids,
            multiIf(c.company_id = 'ftg', 1, c.company_id = 'ftg-academy', 2, 3) AS cix,
            (days.d - toDate('2025-01-01')) / 668. AS tf,
            multiIf(cix = 1, 18 * (1 + 0.6 * tf),
                    cix = 2, 14 * [0.75, 1.30, 2.00, 1.10, 0.80, 0.75, 0.70, 1.30, 2.00, 1.10, 0.80, 0.75][toMonth(days.d)],
                             5 * exp(1.5 * tf))
              * if(cix = 2, 1.0, [0.80, 0.90, 1.00, 1.00, 1.00, 1.05, 1.00, 0.85, 1.05, 1.15, 1.25, 1.30][toMonth(days.d)])
              * [1.10, 1.15, 1.15, 1.10, 1.00, 0.35, 0.25][toDayOfWeek(days.d)]
              * (0.6 + 0.8 * (cityHash64('txn-n', c.company_id, days.d) % 1000000) / 1000000.) AS mu,
            toUInt32(round(mu)) AS n
        FROM (SELECT toDate('2025-01-01') + number AS d FROM numbers(669)) AS days
        CROSS JOIN biz.customers AS c
        WHERE c.acquired_at <= days.d AND (c.churned_at IS NULL OR c.churned_at > days.d)
        GROUP BY c.company_id, days.d
    ) AS e
    ARRAY JOIN range(e.n) AS k
) AS r
INNER JOIN biz.customers AS c ON c.company_id = r.company_id AND c.customer_id = r.customer_id;

-- ───────────────────────── headcount_monthly ─────────────────────────
CREATE TABLE IF NOT EXISTS biz.headcount_monthly
(
    company_id LowCardinality(String),
    month      Date,
    department LowCardinality(String),
    headcount  UInt16,
    payroll    Float64
) ENGINE = MergeTree ORDER BY (company_id, month, department);

INSERT INTO biz.headcount_monthly
WITH
    ['ftg', 'ftg-academy', 'ftg-cloud'] AS cos,
    ['engineering', 'security-research', 'sales', 'marketing', 'education', 'g-and-a'] AS depts,
    [[14, 10, 6, 4, 1, 4], [3, 2, 3, 3, 12, 3], [6, 3, 3, 2, 1, 2]] AS base,
    [7500000, 7000000, 6000000, 5500000, 5500000, 5000000] AS salary,
    mo / 21. AS mf,
    multiIf(ci = 1, 1 + 0.30 * mf, ci = 2, 1 + 0.05 * mf, 1 + 1.5 * mf) AS trend,
    addMonths(toDate('2025-01-01'), mo) AS month_d,
    toUInt16(round(base[ci][di] * trend)) AS hc
SELECT
    cos[ci] AS company_id,
    month_d AS month,
    depts[di] AS department,
    hc AS headcount,
    round(hc * salary[di] * (1 + 0.04 * (toYear(month_d) - 2025)), -3) AS payroll
FROM (SELECT number AS mo FROM numbers(22)) AS months
CROSS JOIN (SELECT number + 1 AS ci FROM numbers(3)) AS companies
CROSS JOIN (SELECT number + 1 AS di FROM numbers(6)) AS departments;

-- ───────────────────────── campaigns ─────────────────────────
CREATE TABLE IF NOT EXISTS biz.campaigns
(
    company_id  LowCardinality(String),
    campaign_id String,
    name        String,
    channel     LowCardinality(String),
    objective   LowCardinality(String),
    start_date  Date,
    end_date    Date,
    budget      Float64
) ENGINE = MergeTree ORDER BY (company_id, campaign_id);

INSERT INTO biz.campaigns
WITH
    ['ftg', 'ftg-academy', 'ftg-cloud'] AS cos,
    ['search', 'social', 'display', 'email', 'event', 'content'] AS chans,
    [150000, 120000, 100000, 20000, 2500000, 60000] AS daily_cost,
    intDiv(number, 40) + 1 AS ci,
    number % 40 AS k,
    k % 8 AS q,                       -- quarter index 0..7 = 2025Q1 .. 2026Q4
    intDiv(k, 8) + 1 AS seq,
    toUInt32(cityHash64('cmp-chan', number) % 6) + 1 AS chi,
    chans[chi] AS chan,
    addMonths(toDate('2025-01-01'), q * 3) AS qstart,
    qstart + toUInt32(cityHash64('cmp-start', number) % if(q = 7, 25, 75)) AS start_d,
    multiIf(chan IN ('search', 'content'), 180 + cityHash64('cmp-dur', number) % 360,
            chan = 'event',                 3 + cityHash64('cmp-dur', number) % 12,
                                           60 + cityHash64('cmp-dur', number) % 270) AS dur,
    least(start_d + toUInt32(dur) - 1, toDate('2026-10-31')) AS end_d,
    (cityHash64('cmp-budget', number) % 1000000) / 1000000. AS u_b,
    multiIf(ci = 1, 1.0, ci = 2, 0.7, 0.5 + 0.8 * q / 7.) AS scale,
    [0.80, 0.90, 1.00, 1.00, 1.00, 1.05, 1.00, 0.85, 1.05, 1.15, 1.25, 1.30][toMonth(start_d)] AS season
SELECT
    cos[ci] AS company_id,
    concat('CP-', leftPad(toString(number + 1), 4, '0')) AS campaign_id,
    concat('CMP-', toString(2025 + intDiv(q, 4)), 'Q', toString(q % 4 + 1), '-', chan, '-', leftPad(toString(seq), 2, '0')) AS name,
    chan AS channel,
    ['awareness', 'lead-gen', 'conversion', 'retention'][toUInt32(cityHash64('cmp-obj', number) % 4) + 1] AS objective,
    start_d AS start_date,
    end_d AS end_date,
    round(daily_cost[chi] * (end_d - start_d + 1) * scale * season * (0.8 + 0.4 * u_b), -4) AS budget
FROM numbers(120);

-- ───────────────────────── marketing_daily ─────────────────────────
CREATE TABLE IF NOT EXISTS biz.marketing_daily
(
    company_id  LowCardinality(String),
    date        Date,
    campaign_id String,
    channel     LowCardinality(String),
    impressions UInt32,
    clicks      UInt32,
    spend       Float64,
    leads       UInt16,
    conversions UInt16
) ENGINE = MergeTree ORDER BY (company_id, date, campaign_id);

INSERT INTO biz.marketing_daily
WITH
    -- per-channel economics: cost per impression (KRW), CTR, lead rate per click, conversion rate per lead
    ['search', 'social', 'display', 'email', 'event', 'content'] AS chans,
    [3.0, 1.5, 0.5, 10.0, 20000.0, 0.8] AS cpi,
    [0.040, 0.012, 0.003, 0.025, 0.150, 0.018] AS ctr,
    [0.08, 0.03, 0.015, 0.10, 0.30, 0.06] AS lead_rate,
    [0.20, 0.12, 0.08, 0.25, 0.35, 0.15] AS conv_rate,
    indexOf(chans, channel) AS chi,
    (cityHash64('mk-lead', campaign_id, date) % 1000000) / 1000000. AS u_l,
    (cityHash64('mk-conv', campaign_id, date) % 1000000) / 1000000. AS u_c,
    -- leads follow clicks with a 1-3 day lag
    toUInt16(round(lead_rate[chi] * (0.1 * clicks + 0.3 * c1 + 0.4 * c2 + 0.2 * c3) * (0.8 + 0.4 * u_l))) AS leads_n
SELECT
    company_id,
    date,
    campaign_id,
    channel,
    impressions,
    clicks,
    spend,
    leads_n AS leads,
    least(leads_n, toUInt16(round(leads_n * conv_rate[chi] * (0.7 + 0.6 * u_c)))) AS conversions
FROM
(
    SELECT
        company_id, campaign_id, channel, date, impressions, clicks, spend,
        lagInFrame(clicks, 1) OVER w AS c1,
        lagInFrame(clicks, 2) OVER w AS c2,
        lagInFrame(clicks, 3) OVER w AS c3
    FROM
    (
        SELECT
            c.company_id,
            c.campaign_id,
            c.channel,
            c.start_date + k AS date,
            indexOf(['search', 'social', 'display', 'email', 'event', 'content'], c.channel) AS chi0,
            round(c.budget / (c.end_date - c.start_date + 1)
                  * [1.00, 1.05, 1.05, 1.05, 0.95, 0.70, 0.65][toDayOfWeek(date)]
                  * (0.7 + 0.6 * (cityHash64('mk-spend', c.campaign_id, date) % 1000000) / 1000000.), -2) AS spend,
            toUInt32(round(spend / [3.0, 1.5, 0.5, 10.0, 20000.0, 0.8][chi0]
                           * (0.9 + 0.2 * (cityHash64('mk-impr', c.campaign_id, date) % 1000000) / 1000000.))) AS impressions,
            toUInt32(round(impressions * [0.040, 0.012, 0.003, 0.025, 0.150, 0.018][chi0]
                           * (0.8 + 0.4 * (cityHash64('mk-click', c.campaign_id, date) % 1000000) / 1000000.))) AS clicks
        FROM biz.campaigns AS c
        ARRAY JOIN range(toUInt32(c.end_date - c.start_date + 1)) AS k
    )
    WINDOW w AS (PARTITION BY campaign_id ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
);

-- ───────────────────────── expenses ─────────────────────────
CREATE TABLE IF NOT EXISTS biz.expenses
(
    company_id   LowCardinality(String),
    expense_id   String,
    expense_date Date,
    department   LowCardinality(String),
    category     LowCardinality(String),
    vendor       LowCardinality(String),
    amount       Float64
) ENGINE = MergeTree ORDER BY (company_id, expense_date, expense_id);

-- payroll: one line per department on the 25th, equal to headcount_monthly.payroll (largest category)
INSERT INTO biz.expenses
SELECT
    company_id,
    concat('E-', substring(hex(cityHash64('exp-pay', company_id, month, department)), 1, 12)) AS expense_id,
    month + 24 AS expense_date,
    department,
    'payroll' AS category,
    'V-001' AS vendor,
    payroll AS amount
FROM biz.headcount_monthly;

-- marketing: one line per campaign per month, equal to the month's marketing_daily.spend
INSERT INTO biz.expenses
SELECT
    company_id,
    concat('E-', substring(hex(cityHash64('exp-mkt', company_id, campaign_id, toStartOfMonth(date))), 1, 12)) AS expense_id,
    toLastDayOfMonth(date) AS expense_date,
    'marketing' AS department,
    'marketing' AS category,
    concat('V-0', toString(9 + indexOf(['search', 'social', 'display', 'email', 'event', 'content'], channel))) AS vendor,
    round(sum(spend), -2) AS amount
FROM biz.marketing_daily
GROUP BY company_id, campaign_id, channel, toStartOfMonth(date), expense_date;

-- rent: monthly on the 1st
INSERT INTO biz.expenses
WITH ['ftg', 'ftg-academy', 'ftg-cloud'] AS cos, addMonths(toDate('2025-01-01'), mo) AS month_d
SELECT
    cos[ci] AS company_id,
    concat('E-', substring(hex(cityHash64('exp-rent', ci, month_d)), 1, 12)) AS expense_id,
    month_d AS expense_date,
    'g-and-a' AS department,
    'rent' AS category,
    'V-002' AS vendor,
    [18000000, 25000000, 9000000][ci] AS amount
FROM (SELECT number AS mo FROM numbers(22)) AS months
CROSS JOIN (SELECT number + 1 AS ci FROM numbers(3)) AS companies;

-- day-to-day spend: infrastructure / software / travel / outsourcing / other
INSERT INTO biz.expenses
WITH
    ['ftg', 'ftg-academy', 'ftg-cloud'] AS cos,
    cos[ci] AS co,
    toDate('2025-01-01') + dn AS d,
    (d - toDate('2025-01-01')) / 668. AS tf,
    multiIf(ci = 1, 1 + 0.4 * tf, ci = 2, 1.0, 1 + 1.6 * tf) AS trend,
    (cityHash64('exp-cat', ci, d, k) % 1000000) / 1000000. AS u_cat,
    (cityHash64('exp-amt', ci, d, k) % 1000000) / 1000000. AS u_amt,
    arrayFirstIndex(x -> u_cat < x, [0.30, 0.50, 0.70, 0.85, 1.0]) AS cat_i,
    ['infrastructure', 'software', 'travel', 'outsourcing', 'other'][cat_i] AS cat,
    multiIf(cat_i = 1, (100000 + u_amt *  700000) * [2.0, 0.6, 1.5][ci] * trend,
            cat_i = 2,   50000 + u_amt *  550000,
            cat_i = 3,   30000 + u_amt *  470000,
            cat_i = 4,  (500000 + u_amt * 4500000) * [1.0, 0.8, 1.4][ci],
                         20000 + u_amt *  280000) AS base_amt,
    -- anomaly: ftg-cloud infrastructure bill jumps from June 2026
    if(ci = 3 AND cat_i = 1 AND d >= toDate('2026-06-01'), 3.2, 1.0) AS jump,
    [['engineering', 'engineering', 'security-research', 'security-research', 'sales', 'marketing', 'g-and-a'],
     ['education', 'education', 'education', 'marketing', 'sales', 'engineering', 'g-and-a'],
     ['engineering', 'engineering', 'sales', 'sales', 'security-research', 'marketing', 'g-and-a']][ci] AS dept_pool,
    multiIf(cat_i = 1, ['engineering', 'engineering', 'security-research'][toUInt32(cityHash64('exp-dept', ci, d, k) % 3) + 1],
                       dept_pool[toUInt32(cityHash64('exp-dept', ci, d, k) % 7) + 1]) AS dept,
    multiIf(cat_i = 1, concat('V-00', toString(3 + cityHash64('exp-vendor', ci, d, k) % 4)),          -- V-003..V-006 cloud providers
                       concat('V-0', toString(20 + cityHash64('exp-vendor', ci, d, k) % 40))) AS vend    -- V-020..V-059
SELECT
    co AS company_id,
    concat('E-', substring(hex(cityHash64('exp-id', ci, d, k)), 1, 12)) AS expense_id,
    d AS expense_date,
    dept AS department,
    cat AS category,
    vend AS vendor,
    round(base_amt * jump, -3) AS amount
FROM (SELECT number AS dn FROM numbers(669)) AS days
CROSS JOIN (SELECT number + 1 AS ci FROM numbers(3)) AS companies
ARRAY JOIN range(toUInt32(5 + cityHash64('exp-n', ci, dn) % 5)) AS k;

-- ───────────────────────── cash_monthly ─────────────────────────
CREATE TABLE IF NOT EXISTS biz.cash_monthly
(
    company_id      LowCardinality(String),
    month           Date,
    opening_balance Float64,
    inflow          Float64,
    outflow         Float64,
    closing_balance Float64
) ENGINE = MergeTree ORDER BY (company_id, month);

-- inflow = paid revenue net of discount; outflow = expenses + refunds. Integer KRW so the chain is exact.
INSERT INTO biz.cash_monthly
SELECT
    company_id,
    month,
    opening_balance,
    inflow,
    outflow,
    opening_balance + inflow - outflow AS closing_balance
FROM
(
    SELECT
        company_id,
        month,
        inflow,
        outflow,
        multiIf(company_id = 'ftg', 1500000000., company_id = 'ftg-academy', 600000000., 2500000000.)
            + sum(inflow - outflow) OVER (PARTITION BY company_id ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS opening_balance
    FROM
    (
        SELECT company_id, month, sum(inflow) AS inflow, sum(outflow) AS outflow
        FROM
        (
            SELECT company_id, toStartOfMonth(txn_date) AS month, round(sum(amount * (1 - discount_rate))) AS inflow, 0. AS outflow
            FROM biz.revenue WHERE status = 'paid' GROUP BY company_id, month
            UNION ALL
            SELECT company_id, toStartOfMonth(txn_date) AS month, 0. AS inflow, round(sum(amount * (1 - discount_rate))) AS outflow
            FROM biz.revenue WHERE status = 'refunded' GROUP BY company_id, month
            UNION ALL
            SELECT company_id, toStartOfMonth(expense_date) AS month, 0. AS inflow, round(sum(amount)) AS outflow
            FROM biz.expenses GROUP BY company_id, month
        )
        GROUP BY company_id, month
    )
);

-- ───────────────────────── fx_rates (no tenant column) ─────────────────────────
CREATE TABLE IF NOT EXISTS biz.fx_rates
(
    rate_date    Date,
    currency     LowCardinality(String),
    krw_per_unit Float64
) ENGINE = MergeTree ORDER BY (currency, rate_date);

INSERT INTO biz.fx_rates
SELECT
    rate_date,
    currency,
    round(base * (1 + 0.03 * sin(t / 90.)) + walk, 2) AS krw_per_unit
FROM
(
    SELECT
        toDate('2025-01-01') + number AS rate_date,
        number AS t,
        currency,
        multiIf(currency = 'USD', 1420., currency = 'EUR', 1540., 9.4) AS base,
        sum(((cityHash64('fx', currency, number) % 1000000) / 1000000. - 0.5) * base * 0.004)
            OVER (PARTITION BY currency ORDER BY number ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS walk
    FROM numbers(669) AS days
    CROSS JOIN (SELECT arrayJoin(['USD', 'EUR', 'JPY']) AS currency) AS currencies
);
