"""Interactive Web Dashboard for Callora Analytics Agent.

Provides a responsive UI with glassmorphism aesthetics, KPI cards,
funnel drop-off charts, objection breakdowns, sentiment analysis, and call insights.
"""


def render_analytics_dashboard(business_id: str = "biz_001") -> str:
    """Generate the self-contained HTML/CSS/JS analytics dashboard."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Callora | Analytics & Sales Leakage Intelligence</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-base: #0a0d14;
            --bg-surface: rgba(18, 24, 38, 0.7);
            --bg-card: rgba(22, 30, 49, 0.65);
            --bg-card-hover: rgba(30, 41, 67, 0.85);
            --border-color: rgba(255, 255, 255, 0.08);
            --border-glow: rgba(99, 102, 241, 0.3);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent-primary: #6366f1;
            --accent-glow: rgba(99, 102, 241, 0.5);
            --accent-success: #10b981;
            --accent-warning: #f59e0b;
            --accent-danger: #ef4444;
            --accent-purple: #a855f7;
            --accent-cyan: #06b6d4;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: var(--bg-base);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 24px;
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(99, 102, 241, 0.12) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(16, 185, 129, 0.08) 0%, transparent 45%),
                radial-gradient(circle at 50% 50%, rgba(6, 182, 212, 0.05) 0%, transparent 60%);
            background-attachment: fixed;
        }}

        .container {{
            max-width: 1440px;
            margin: 0 auto;
        }}

        /* Header */
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px 28px;
            background: var(--bg-surface);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            margin-bottom: 24px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.37);
        }}

        .brand {{
            display: flex;
            align-items: center;
            gap: 16px;
        }}

        .logo-icon {{
            width: 44px;
            height: 44px;
            background: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 22px;
            box-shadow: 0 4px 20px rgba(99, 102, 241, 0.4);
        }}

        .brand-text h1 {{
            font-size: 22px;
            font-weight: 800;
            letter-spacing: -0.5px;
            background: linear-gradient(to right, #ffffff, #94a3b8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}

        .brand-text p {{
            font-size: 13px;
            color: var(--text-secondary);
        }}

        .header-controls {{
            display: flex;
            align-items: center;
            gap: 14px;
        }}

        .live-badge {{
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(16, 185, 129, 0.12);
            border: 1px solid rgba(16, 185, 129, 0.3);
            color: #34d399;
            padding: 6px 14px;
            border-radius: 9999px;
            font-size: 13px;
            font-weight: 600;
        }}

        .live-dot {{
            width: 8px;
            height: 8px;
            background: #10b981;
            border-radius: 50%;
            box-shadow: 0 0 12px #10b981;
            animation: pulse 2s infinite;
        }}

        @keyframes pulse {{
            0% {{ transform: scale(0.95); opacity: 0.8; }}
            50% {{ transform: scale(1.2); opacity: 1; }}
            100% {{ transform: scale(0.95); opacity: 0.8; }}
        }}

        .btn {{
            background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%);
            color: white;
            border: none;
            padding: 10px 18px;
            border-radius: 10px;
            font-weight: 600;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 8px;
            box-shadow: 0 4px 14px rgba(79, 70, 229, 0.35);
        }}

        .btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(79, 70, 229, 0.5);
        }}

        .btn-secondary {{
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            box-shadow: none;
        }}

        .btn-secondary:hover {{
            background: rgba(255, 255, 255, 0.1);
            box-shadow: none;
        }}

        /* KPI Grid */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 18px;
            margin-bottom: 24px;
        }}

        .kpi-card {{
            background: var(--bg-card);
            backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 18px;
            padding: 22px;
            position: relative;
            overflow: hidden;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }}

        .kpi-card:hover {{
            transform: translateY(-4px);
            border-color: var(--border-glow);
            box-shadow: 0 12px 30px rgba(0, 0, 0, 0.45);
        }}

        .kpi-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, transparent, var(--card-accent, #6366f1), transparent);
            opacity: 0.8;
        }}

        .kpi-title {{
            font-size: 13px;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .kpi-value {{
            font-size: 28px;
            font-weight: 800;
            letter-spacing: -0.5px;
            margin-bottom: 6px;
            color: var(--text-primary);
        }}

        .kpi-subtitle {{
            font-size: 12px;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .badge-positive {{ color: #34d399; font-weight: 600; }}
        .badge-warning {{ color: #fbbf24; font-weight: 600; }}
        .badge-danger {{ color: #f87171; font-weight: 600; }}

        /* Main Section Grid */
        .analytics-grid {{
            display: grid;
            grid-template-columns: 7fr 5fr;
            gap: 24px;
            margin-bottom: 24px;
        }}

        @media (max-width: 1024px) {{
            .analytics-grid {{ grid-template-columns: 1fr; }}
        }}

        .card {{
            background: var(--bg-card);
            backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 18px;
            padding: 24px;
        }}

        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--border-color);
        }}

        .card-header h2 {{
            font-size: 17px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        /* Funnel Stages */
        .funnel-stage {{
            margin-bottom: 18px;
        }}

        .funnel-stage-header {{
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 8px;
        }}

        .funnel-bar-bg {{
            height: 32px;
            background: rgba(255, 255, 255, 0.04);
            border-radius: 8px;
            overflow: hidden;
            position: relative;
            display: flex;
            align-items: center;
            border: 1px solid rgba(255, 255, 255, 0.06);
        }}

        .funnel-bar-fill {{
            height: 100%;
            background: linear-gradient(90deg, #4f46e5 0%, #06b6d4 100%);
            border-radius: 6px;
            transition: width 1s cubic-bezier(0.4, 0, 0.2, 1);
            display: flex;
            align-items: center;
            padding-left: 12px;
            color: white;
            font-size: 12px;
            font-weight: 700;
        }}

        .funnel-drop-tag {{
            position: absolute;
            right: 12px;
            font-size: 11px;
            color: #f87171;
            font-weight: 600;
            background: rgba(239, 68, 68, 0.15);
            padding: 3px 8px;
            border-radius: 6px;
        }}

        /* Loss Reasons Bar */
        .reason-row {{
            margin-bottom: 14px;
        }}

        .reason-meta {{
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            margin-bottom: 6px;
        }}

        .reason-meta span:first-child {{
            font-weight: 600;
            color: var(--text-primary);
        }}

        .reason-bar {{
            height: 8px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 4px;
            overflow: hidden;
        }}

        .reason-fill {{
            height: 100%;
            border-radius: 4px;
            background: linear-gradient(90deg, #f59e0b, #ef4444);
        }}

        /* AI Recommendations Box */
        .recommendations-box {{
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.08) 0%, rgba(168, 85, 247, 0.05) 100%);
            border: 1px solid rgba(99, 102, 241, 0.25);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 24px;
        }}

        .rec-item {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            margin-top: 12px;
            font-size: 13px;
            line-height: 1.5;
            color: #cbd5e1;
        }}

        .rec-icon {{
            background: rgba(99, 102, 241, 0.2);
            color: #818cf8;
            padding: 4px;
            border-radius: 6px;
            font-size: 14px;
        }}

        /* Call Insights Table */
        .table-responsive {{
            overflow-x: auto;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}

        th {{
            text-align: left;
            padding: 12px 14px;
            background: rgba(255, 255, 255, 0.03);
            color: var(--text-secondary);
            font-weight: 600;
            border-bottom: 1px solid var(--border-color);
        }}

        td {{
            padding: 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            vertical-align: middle;
        }}

        tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
        }}

        .tag {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
        }}

        .tag-positive {{ background: rgba(16, 185, 129, 0.15); color: #34d399; }}
        .tag-neutral {{ background: rgba(148, 163, 184, 0.15); color: #cbd5e1; }}
        .tag-negative {{ background: rgba(239, 68, 68, 0.15); color: #f87171; }}
        .tag-intent {{ background: rgba(99, 102, 241, 0.15); color: #818cf8; }}

        .code-pill {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            background: rgba(0, 0, 0, 0.3);
            padding: 2px 6px;
            border-radius: 4px;
            color: #93c5fd;
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <header>
            <div class="brand">
                <div class="logo-icon">📊</div>
                <div class="brand-text">
                    <h1>Callora Analytics Agent</h1>
                    <p>Real-Time Sales Leakage, Funnel Conversion & Conversation Intelligence</p>
                </div>
            </div>
            <div class="header-controls">
                <div class="live-badge">
                    <div class="live-dot"></div>
                    <span>Active Telemetry</span>
                </div>
                <button class="btn btn-secondary" onclick="viewApiDocs()">API Specs</button>
                <button class="btn" onclick="fetchAnalyticsData()">🔄 Refresh</button>
            </div>
        </header>

        <!-- KPI Cards -->
        <div class="kpi-grid">
            <div class="kpi-card" style="--card-accent: #10b981;">
                <div class="kpi-title">
                    <span>Revenue Recovered</span>
                    <span>💰</span>
                </div>
                <div class="kpi-value" id="kpi-recovered-revenue">₹0</div>
                <div class="kpi-subtitle">
                    <span class="badge-positive" id="kpi-recovery-rate">0%</span>
                    <span>lead recovery conversion</span>
                </div>
            </div>

            <div class="kpi-card" style="--card-accent: #ef4444;">
                <div class="kpi-title">
                    <span>Sales Leakage</span>
                    <span>📉</span>
                </div>
                <div class="kpi-value" id="kpi-leakage-value">₹0</div>
                <div class="kpi-subtitle">
                    <span class="badge-danger" id="kpi-leakage-rate">0%</span>
                    <span>pipeline value lost</span>
                </div>
            </div>

            <div class="kpi-card" style="--card-accent: #6366f1;">
                <div class="kpi-title">
                    <span>Qualification Rate</span>
                    <span>🎯</span>
                </div>
                <div class="kpi-value" id="kpi-qualification-rate">0%</div>
                <div class="kpi-subtitle">
                    <span id="kpi-qualified-leads">0</span>
                    <span>leads qualified on calls</span>
                </div>
            </div>

            <div class="kpi-card" style="--card-accent: #a855f7;">
                <div class="kpi-title">
                    <span>Outreach Telephony Spend</span>
                    <span>📞</span>
                </div>
                <div class="kpi-value" id="kpi-call-cost">₹0</div>
                <div class="kpi-subtitle">
                    <span>CAC per recovered lead:</span>
                    <span id="kpi-cac">₹0</span>
                </div>
            </div>

            <div class="kpi-card" style="--card-accent: #06b6d4;">
                <div class="kpi-title">
                    <span>Recovery ROI Multiple</span>
                    <span>🚀</span>
                </div>
                <div class="kpi-value" id="kpi-roi">0x</div>
                <div class="kpi-subtitle">
                    <span>Net protected value:</span>
                    <span id="kpi-net-value">₹0</span>
                </div>
            </div>

            <div class="kpi-card" style="--card-accent: #f59e0b;">
                <div class="kpi-title">
                    <span>Customer Sentiment</span>
                    <span>💬</span>
                </div>
                <div class="kpi-value" id="kpi-sentiment">0% Pos</div>
                <div class="kpi-subtitle">
                    <span id="kpi-total-calls">0</span>
                    <span>calls analyzed</span>
                </div>
            </div>
        </div>

        <!-- AI Recommendations -->
        <div class="recommendations-box">
            <h3 style="font-size: 15px; font-weight: 700; color: #c7d2fe; display: flex; align-items: center; gap: 8px;">
                <span>💡</span> AI Sales Leakage Mitigation Recommendations
            </h3>
            <div id="recommendations-list">
                <div class="rec-item">
                    <div class="rec-icon">⚡</div>
                    <div>Analyzing funnel drop-offs and objection patterns across conversations...</div>
                </div>
            </div>
        </div>

        <!-- Funnel and Objections Grid -->
        <div class="analytics-grid">
            <!-- Funnel Stages -->
            <div class="card">
                <div class="card-header">
                    <h2><span>🔻</span> Sales Funnel Conversion & Stage Leakage</h2>
                    <span style="font-size: 12px; color: var(--text-muted);">End-to-End Outreach Pipeline</span>
                </div>
                <div id="funnel-container">
                    <p style="color: var(--text-muted); font-size: 13px;">Loading funnel metrics...</p>
                </div>
            </div>

            <!-- Grouped Refusals & Objections -->
            <div class="card">
                <div class="card-header">
                    <h2><span>🛑</span> Primary Objection & Loss Reasons</h2>
                    <span style="font-size: 12px; color: var(--text-muted);">Impact by Frequency</span>
                </div>
                <div id="reasons-container">
                    <p style="color: var(--text-muted); font-size: 13px;">Loading refusal groupings...</p>
                </div>
            </div>
        </div>

        <!-- Call Insights Table -->
        <div class="card">
            <div class="card-header">
                <h2><span>🎧</span> Per-Call Conversation Intelligence & Objections</h2>
                <div style="display: flex; gap: 8px;">
                    <button class="btn btn-secondary" style="padding: 6px 12px; font-size: 12px;" onclick="filterSentiment('')">All</button>
                    <button class="btn btn-secondary" style="padding: 6px 12px; font-size: 12px;" onclick="filterSentiment('positive')">Positive</button>
                    <button class="btn btn-secondary" style="padding: 6px 12px; font-size: 12px;" onclick="filterSentiment('neutral')">Neutral</button>
                    <button class="btn btn-secondary" style="padding: 6px 12px; font-size: 12px;" onclick="filterSentiment('negative')">Negative</button>
                </div>
            </div>
            <div class="table-responsive">
                <table>
                    <thead>
                        <tr>
                            <th>Call ID</th>
                            <th>Lead</th>
                            <th>Duration</th>
                            <th>Disposition</th>
                            <th>Customer Intent</th>
                            <th>Sentiment</th>
                            <th>Detected Objections</th>
                            <th>Refusal Reason</th>
                        </tr>
                    </thead>
                    <tbody id="calls-table-body">
                        <tr>
                            <td colspan="8" style="text-align: center; color: var(--text-muted); padding: 24px;">Loading conversation telemetry...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        const BIZ_ID = "{business_id}";
        let cachedCalls = [];

        function formatINR(val) {{
            if (!val || val === 0) return "₹0";
            return "₹" + Number(val).toLocaleString("en-IN", {{ maximumFractionDigits: 0 }});
        }}

        async function fetchAnalyticsData() {{
            try {{
                const [summaryRes, leakageRes, convRes] = await Promise.all([
                    fetch(`/analytics/summary?business_id=${{BIZ_ID}}`),
                    fetch(`/analytics/leakage?business_id=${{BIZ_ID}}`),
                    fetch(`/analytics/conversations?business_id=${{BIZ_ID}}&limit=50`)
                ]);

                const summary = await summaryRes.json();
                const leakage = await leakageRes.json();
                const conv = await convRes.json();

                renderKPIs(summary, leakage, conv);
                renderRecommendations(leakage.actionable_recommendations);
                renderFunnel(leakage.stages);
                renderLossReasons(leakage.loss_reasons);
                cachedCalls = conv.call_insights || [];
                renderCalls(cachedCalls);
            }} catch (err) {{
                console.error("Failed to load analytics telemetry:", err);
            }}
        }}

        function renderKPIs(summary, leakage, conv) {{
            const fin = summary.financials || {{}};
            const rates = summary.rates_and_kpis || {{}};
            const rec = summary.recovery_metrics || {{}};

            document.getElementById("kpi-recovered-revenue").innerText = formatINR(fin.recorded_revenue_recovered);
            document.getElementById("kpi-recovery-rate").innerText = (rec.recovery_rate_pct || 0) + "%";

            document.getElementById("kpi-leakage-value").innerText = formatINR(leakage.total_lost_revenue);
            document.getElementById("kpi-leakage-rate").innerText = (leakage.overall_leakage_rate_pct || 0) + "%";

            document.getElementById("kpi-qualification-rate").innerText = (rates.qualification_rate_pct || 0) + "%";
            document.getElementById("kpi-qualified-leads").innerText = summary.lead_funnel?.leads_qualified || 0;

            document.getElementById("kpi-call-cost").innerText = formatINR(fin.total_call_cost);
            document.getElementById("kpi-cac").innerText = formatINR(fin.cost_per_recovered_opportunity);

            document.getElementById("kpi-roi").innerText = (fin.roi_multiple || 0) + "x";
            document.getElementById("kpi-net-value").innerText = formatINR(fin.net_revenue_recovered);

            const sent = conv.sentiment_breakdown || {{}};
            document.getElementById("kpi-sentiment").innerText = (sent.positive_pct || 0) + "% Pos";
            document.getElementById("kpi-total-calls").innerText = conv.total_conversations || 0;
        }}

        function renderRecommendations(recs) {{
            const container = document.getElementById("recommendations-list");
            if (!recs || recs.length === 0) {{
                container.innerHTML = '<div class="rec-item"><div class="rec-icon">✅</div><div>Funnel health is stable with no critical leaks identified.</div></div>';
                return;
            }}
            container.innerHTML = recs.map(r => `
                <div class="rec-item">
                    <div class="rec-icon">⚡</div>
                    <div>${{r}}</div>
                </div>
            `).join("");
        }}

        function renderFunnel(stages) {{
            const container = document.getElementById("funnel-container");
            if (!stages || stages.length === 0) {{
                container.innerHTML = '<p style="color: var(--text-muted); font-size: 13px;">No funnel stages recorded yet.</p>';
                return;
            }}
            const maxEntered = Math.max(...stages.map(s => s.entered), 1);
            container.innerHTML = stages.map(s => {{
                const pctWidth = Math.max(12, Math.round((s.entered / maxEntered) * 100));
                return `
                    <div class="funnel-stage">
                        <div class="funnel-stage-header">
                            <span>${{s.stage_name}}</span>
                            <span style="color: var(--text-secondary);">${{s.converted}} / ${{s.entered}} (${{s.conversion_rate_pct}}%)</span>
                        </div>
                        <div class="funnel-bar-bg">
                            <div class="funnel-bar-fill" style="width: ${{pctWidth}}%;">
                                ${{s.entered}}
                            </div>
                            ${{s.dropped > 0 ? `<div class="funnel-drop-tag">-${{s.dropped}} dropped (${{s.drop_off_rate_pct}}% | -${{formatINR(s.lost_deal_value)}})</div>` : ''}}
                        </div>
                    </div>
                `;
            }}).join("");
        }}

        function renderLossReasons(reasons) {{
            const container = document.getElementById("reasons-container");
            if (!reasons || reasons.length === 0) {{
                container.innerHTML = '<p style="color: var(--text-muted); font-size: 13px;">No loss reasons recorded yet.</p>';
                return;
            }}
            container.innerHTML = reasons.slice(0, 6).map(r => `
                <div class="reason-row">
                    <div class="reason-meta">
                        <span>${{r.reason}}</span>
                        <span style="color: #fca5a5; font-weight: 600;">${{r.count}} (${{r.percentage_of_total_losses}}%)</span>
                    </div>
                    <div class="reason-bar">
                        <div class="reason-fill" style="width: ${{Math.min(100, Math.max(8, r.percentage_of_total_losses))}}%;"></div>
                    </div>
                </div>
            `).join("");
        }}

        function renderCalls(calls) {{
            const tbody = document.getElementById("calls-table-body");
            if (!calls || calls.length === 0) {{
                tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 24px;">No call conversations found.</td></tr>';
                return;
            }}
            tbody.innerHTML = calls.map(c => `
                <tr>
                    <td><span class="code-pill">${{c.call_id}}</span></td>
                    <td style="font-weight: 600;">${{c.lead_name || 'Anonymous'}}</td>
                    <td>${{c.duration_seconds}}s</td>
                    <td><span class="tag tag-${{c.qualified ? 'positive' : 'neutral'}}">${{c.disposition}}</span></td>
                    <td><span class="tag tag-intent">${{c.customer_intent || 'general'}}</span></td>
                    <td><span class="tag tag-${{c.sentiment}}">${{c.sentiment}} (${{c.sentiment_score}})</span></td>
                    <td>${{c.objections && c.objections.length ? c.objections.map(o => `<span class="tag tag-negative" style="margin: 2px;">${{o}}</span>`).join('') : '<span style="color: var(--text-muted);">None</span>'}}</td>
                    <td style="color: #f87171; font-size: 12px;">${{c.refusal_reason || c.refusal_category || '-'}}</td>
                </tr>
            `).join("");
        }}

        function filterSentiment(sent) {{
            if (!sent) {{
                renderCalls(cachedCalls);
            }} else {{
                renderCalls(cachedCalls.filter(c => c.sentiment === sent));
            }}
        }}

        function viewApiDocs() {{
            window.open('/docs#/Analytics', '_blank');
        }}

        // Initial fetch on mount
        fetchAnalyticsData();
    </script>
</body>
</html>
"""
