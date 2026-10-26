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
      //Warning Screen
    );
  }

  //main website code 
  return (
    <div>
      {/*Header title*/}
      <h1>Engine Tuning Software</h1> 

      <form>
        <div>
          {/* Stores and updates the elevation entered by the user */}
          <label>Elevation:</label>
          <input 
            type="number" 
            min="-400"
            max="8000"
            step="any"
            required
            value={elevation} //display current elevation value
            onChange={(event) => setElevation(event.target.value)} //update elevation value when user enters it 
          />
        </div>

        <div>
          <label>Idle RPM:</label>
          <input 
            type="number" //only allow numeric 
            min="500"
            max="1000"
            step="1"
            required
            value={idleRPM} //display current idleRPM
            onChange={(event) => setIdleRPM(event.target.value)} //update idleRPM value when user enters it 
          />
        </div>

        <div>
          <label>Redline RPM:</label>
          <input 
            type="number" //only allow numeric input
            min="5000"
            max="10000"
            step="1"
            required
            value={redLineRPM} //display current idleRPM
            onChange={(event) => setRedLineRPM(event.target.value)} //update redLineRPM value when user enters it 
          />
        </div>

        <div>
          <label>Naturally Aspirated Horsepower: </label>
          <input 
            type="number" //only allow numeric input
            min="1"
            step="any"
            required
            value={horsepower} //display current idleRPM
            onChange={(event) => setHorsepower(event.target.value)} //update horsepower value when user enters it 
          />
        </div>

        <div>
          <label>Target Boost PSI:</label>
          <input 
            type="number" //only allow numeric input
            max="45"
            step="any"
            required
            value={boostPSI} //display current idleRPM
            onChange={(event) => setBoostPSI(event.target.value)} //update psi value when user enters it 
          />
        </div>

        <button type="submit">Run Simulation</button>
      </form>
    </div>
  )
}

export default App