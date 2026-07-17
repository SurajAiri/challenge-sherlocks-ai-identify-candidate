# How to Use Sherlock

This guide walks you through the steps to get the Sherlock Candidate Identifier System up and running.

## 1. Prerequisites

Before you begin, ensure you have the following installed on your machine:

- **Node.js** (v18 or higher) and **pnpm**
- **Python** (v3.12 or higher) and **uv**

## 2. Installation

First, install the necessary dependencies from the root directory of the project:

```sh
pnpm install
```

This will install the required packages and set up the monorepo workspace.

## 3. Running the System

Sherlock consists of three main applications that work together: the Web Dashboard, the Belief Engine, and the Scenario Simulator. 

The easiest way to run the entire system is to start all three apps in parallel. From the root directory, run:

```sh
pnpm dev
```

This command will spin up:
- **Dashboard (`apps/web`):** Accessible in your browser at [http://localhost:3000](http://localhost:3000)
- **Belief Engine (`apps/engine`):** Runs the WebSocket service at `ws://localhost:8000`
- **Scenario Simulator (`apps/simulator`):** Runs the WebSocket service at `ws://localhost:8001`

## 4. Running Components Individually

If you need to test or run the Python components separately, you can navigate to their respective directories:

### Run the Belief Engine Only
```sh
cd apps/engine
uv run python -m engine
```

### Run the Simulator Only
```sh
cd apps/simulator
uv run python main.py
```

## 5. Interacting with the App

Once everything is running via `pnpm dev`:
1. Open [http://localhost:3000](http://localhost:3000) in your web browser.
2. Select a simulated meeting scenario from the **Scenario Library**.
3. Watch as the simulator streams events, the transcript populates, and the engine updates its live predictions in the side panel!

## Further Reading

- [Simulator Documentation](simulator.md)
- [Engine Documentation](engine.md)
- [Scenario Authoring Guide](../apps/simulator/docs/SCENARIO_AUTHORING.md)
