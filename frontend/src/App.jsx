function App() {
  return (
    <div>
      <h1>Engine Tuning Software</h1>

      <form>
        <div>
          <label>Elevation:</label>
          <input type="number" />
        </div>

        <div>
          <label>Idle RPM:</label>
          <input type="number" />
        </div>

        <div>
          <label>Redline RPM:</label>
          <input type="number" />
        </div>

        <div>
          <label>Naturally Aspirated Horsepower:</label>
          <input type="number" />
        </div>

        <div>
          <label>Target Boost PSI:</label>
          <input type="number" />
        </div>

        <button type="submit">Run Simulation</button>
      </form>
    </div>
  )
}

export default App