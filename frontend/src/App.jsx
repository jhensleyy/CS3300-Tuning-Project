import { useState } from "react"; //import useState from React so we can store and update user inputs 

function App() {
  //checks whether user has accepted warning
  const [disclaimerAccepted, setDisclaimerAccepted] = useState(false); 

  //store elevation input from user and update value
  const [elevation, setElevation] = useState("");  

  //stores idle RPM entered by the user
  const [idleRPM, setIdleRPM] = useState(""); 

  //stores redline RPM entered by the user
  const [redLineRPM, setRedLineRPM] = useState(""); 

  //stores horsepower entered by the user
  const [horsepower, setHorsepower] = useState(""); 

   //stores boostPSI entered by the user
  const [boostPSI, setBoostPSI] = useState("");

  //stores fuel type chosen by user
  const [fuelType, setFuelType] = useState("");

  //stores graph return by Python
  const [graphURL, setGraphURL] = useState(null);

  //checks if simulation is running
  const [isRunning, setIsRunning] = useState(false);
  

  
  //constants used to calculate atomspheric pressire
  const P0_kpa = 101.325;
  const L = 0.0065;
  const T0 = 288.15;
  const g = 9.80665;
  const M = 0.0289644;
  const R = 8.31447;

  //converts kilopascals to PSI
  const KPA_TO_PSI = 0.14503773773;

  //calculates atmospheric pressure in kPa based on elevation
  const pressure_kpa = P0_kpa * (1 - L * Number(elevation) / T0) ** (g * M / (R * L));

  //converts the calculated atomspheric pressure to PSI 
  const atomsphericPressurePSI = pressure_kpa * KPA_TO_PSI;

  //calculates the minimum boost PSI on atomspheric pressure
  const minBoostPSI = Math.round(atomsphericPressurePSI);

  //collects the engine settings when the user submits the form
  async function handleSubmit(event) {
    //prevents website from reloading
    event.preventDefault();
    
    setIsRunning(true);

    //stores the engine settings entered by the use
    //converts strings into number
    const engineSettings = { 
      elevation: Number(elevation),
      idleRPM: Number(idleRPM),
      redLineRPM: Number(redLineRPM),
      horsepower: Number(horsepower),
      boostPSI: Number(boostPSI),
      fuelType: fuelType
    };

    try {
      // Sends the settings to FastAPI
      const response = await fetch("http://127.0.0.1:8000/simulate-graph", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(engineSettings)
      });

      //checks if the backend accepted the settings
      if (!response.ok) {
        throw new Error("Backend rejected the settings");
      }
        //gets the graph image returned by FastAPI
        const imageBlob = await response.blob();

        //checks the image type and size returned by FastAPI
        console.log("Graph type:", imageBlob.type);
        console.log("Graph size:", imageBlob.size);

        //creates a temporary URL for the graph
        const imageURL = URL.createObjectURL(imageBlob);

      //stores the graph URL so React can display it
        setGraphURL(imageURL);

    } catch (error) {
      //displays an error if the request fails
      console.error("Simulation request failed:", error);
    } finally {
      //stops loading message when request is finished
      setIsRunning(false);
    }
  }

  //Warning code
  if(!disclaimerAccepted) {
    return (
      <div>
        {/*Disclaimer title*/}
        <h1>Engine Tuning Software Disclaimer</h1>
        <p>
          This software does NOT guarantee 100% accuracy or
          safety of its calculations or results. 
        </p>

        <p>
          To the fullest extent permitted by applicable law, the developers
          and contributors are not responsible for any engine damage, mechanical 
          failure, financial loss, personal injury, or other damages resulting
          from the use of or misuse of this software.
        </p>

        <p>
          Users are responsible for verifying all results before making 
          modifications to their vehicles. All use of this software is 
          at the user's own risk.
        </p>

        <button onClick={() => setDisclaimerAccepted(true)}> 
          I Understand and Continue
        </button>
      </div>
    );
  }

  //main website code 
  return (
    <div>
      {/*Header title*/}
      <h1>Engine Tuning Software</h1> 

      {/* runs handleSubmit when the use submits the*/}  
      <form onSubmit={handleSubmit}>
        <div>
          {/* Stores and updates the elevation entered by the user */}
          <label>Elevation (Meters) [-400 to 8000]: </label>
          <input 
            type="number" 
            min="-400"
            max="8000"
            step="any"
            required
            value={elevation}
            onChange={(event) => setElevation(event.target.value)} 
          />
        </div>

        <div>
          <label>Idle RPM [500 to 1000]: </label>
          {/* Stores and updates the idle RPM entered by the user */}
          <input 
            type="number"
            min="500"
            max="1000"
            step="1"
            required
            value={idleRPM} 
            onChange={(event) => setIdleRPM(event.target.value)} 
          />
        </div>

        <div>
          <label>Redline RPM [5000 to 10000]: </label>
          {/* Stores and updates the Redline RPM entered by the user */}
          <input 
            type="number" 
            min="5000"
            max="10000"
            step="1"
            required
            value={redLineRPM}
            onChange={(event) => setRedLineRPM(event.target.value)} 
          />
        </div>

        <div>
          <label>Naturally Aspirated Horsepower [Must be greater than 1]: </label>
          {/* Stores and updates the Horsepower entered by the user */}
          <input 
            type="number"
            min="1"
            step="any"
            required
            value={horsepower} 
            onChange={(event) => setHorsepower(event.target.value)} 
          />
        </div>

        <div>
          <label>Target Boost PSI:</label>
          {/* Shows how to calculate target boost PSI */}
          <details style={{ display: "inline-block", marginLeft: "8px", position: "relative" }}>
            <summary style={{ cursor: "pointer", border: "1px solid gray", borderRadius: "4px", padding: "2px 8px" }}>
              ?
            </summary>

            {/* Displays the explanation when clicked */}
            <div style={{ position: "absolute", background: "#333", color: "white", padding: "12px", width: "280px", zIndex: 1, borderRadius: "5px", textAlign: "left" }}>
              <p>
                To find your target boost PSI, add the boost pressure
                from your system to the atmospheric pressure.
              </p>

              <p>
                Example: If atmospheric pressure is 10 PSI and your
                boost system provides 6 PSI, your target boost
                pressure would be 16 PSI.
              </p>
            </div>
          </details>
          
          {/* Stores and updates the BoostPSI entered by the user */}
          <input 
            type="number"
            min={minBoostPSI}
            max="45"
            step="any"
            required
            value={boostPSI} 
            onChange={(event) => setBoostPSI(event.target.value)} 
          />
        </div>

        <div>
          <label>Fuel Type:</label>
          {/* Stores and updates the fuel type chosen by the user */}
          <select 
            required
            value={fuelType} 
            onChange={(event) => setFuelType(event.target.value)} 
          >
            <option value ="">Select Fuel Type</option>
            <option value ="91">91 Octane</option>
            <option value ="93">93 Octane</option>
            <option value ="E85">E85</option>
          </select>
        </div>

        <button type="submit" disabled={isRunning}>
          {isRunning ? "Running Simulation..." : "Run Simulation"}
        </button>

        {/* Displays a message while the simulation runs */}
        {isRunning && (
          <p>Simulation in progress. Please wait for the results.</p>
        )}

      </form>
      {/* Displays the graph after the simulation finishes */}
      {graphURL && (
        <div>
          <h2>Engine Simulation Results</h2>

          {/* Displays the MAP graph returned by Python */}
          <img
            src={graphURL}
            alt="Engine MAP Simulation Graph"
            style={{ width: "100%", maxWidth: "1000px" }}
          />
        </div>
      )}
    </div>
  )
}

export default App