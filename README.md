# Mobile-First Algorithmic Trading & Arbitrage Platform

A modular, mobile-first algorithmic trading and cross-exchange arbitrage system integrated with Angel One SmartAPI and powered by a FastAPI backend.

## Architecture & Directory Structure
* `backend/`: Core FastAPI server, order execution, market data fetching, and strategy engines.
* `web/dashboard/`: Responsive Tailwind CSS web terminal for real-time monitoring and LTP checking.
* `mobile/android/`: Android native client application (Kotlin) for on-the-go tracking and alerts.

## Key Features
* **Live LTP Inspector**: Real-time market data monitoring across exchanges.
* **Arbitrage Evaluator**: Instant calculation of price spreads and cross-exchange opportunities.
* **FastAPI Backend**: High-performance asynchronous routes for automated execution.
