<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology|Labeling" version="3.34.0" labelsEnabled="1">
  <renderer-v2 type="RuleRenderer" forceraster="0" enableorderby="0">
    <rules key="root">
      <rule key="criteria" filter="&quot;d3_forced_threshold&quot; = 0" label="eligible (criteria path)" symbol="0"/>
      <rule key="forced" filter="&quot;d3_forced_threshold&quot; = 1" label="eligible under forced-144 threshold (L4 disclosure)" symbol="1"/>
    </rules>
    <symbols>
      <symbol type="marker" name="0" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="0,114,178,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_style" type="QString" value="solid"/>
            <Option name="size" type="QString" value="2.6"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="marker" name="1" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="0,114,178,255"/>
            <Option name="outline_color" type="QString" value="213,94,0,255"/>
            <Option name="outline_style" type="QString" value="dash"/>
            <Option name="outline_width" type="QString" value="0.8"/>
            <Option name="size" type="QString" value="2.6"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="rule-based">
    <rules key="labelroot">
      <rule key="shared" filter="&quot;shared_footprint_site_count&quot; &gt; 1"
            description="L17: footprint-level series shared with other MINEDEX sites">
        <settings>
          <text-style fieldName="'shared with ' || (&quot;shared_footprint_site_count&quot; - 1) || ' other sites'"
                      isExpression="1" fontSize="8"/>
        </settings>
      </rule>
    </rules>
  </labeling>
</qgis>
