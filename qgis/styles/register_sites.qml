<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology" version="3.34.0">
  <renderer-v2 type="categorizedSymbol" attr="trajectory_status" forceraster="0" enableorderby="0">
    <categories>
      <category value="eligible" symbol="0" label="eligible (Tier 1 domain)" render="true"/>
      <category value="insufficient_pixel_support" symbol="1" label="insufficient pixel support" render="true"/>
      <category value="no_usable_footprint" symbol="2" label="no usable footprint" render="true"/>
      <category value="crosswalk_not_high_confidence" symbol="3" label="crosswalk not high confidence" render="true"/>
      <category value="threshold_not_computed" symbol="4" label="threshold not computed" render="true"/>
    </categories>
    <symbols>
      <symbol type="marker" name="0" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="0,114,178,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="size" type="QString" value="2.2"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="marker" name="1" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="230,159,0,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="size" type="QString" value="1.6"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="marker" name="2" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="204,121,167,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="size" type="QString" value="1.6"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="marker" name="3" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="86,180,233,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="size" type="QString" value="1.6"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="marker" name="4" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="153,153,153,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="size" type="QString" value="1.6"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
</qgis>
